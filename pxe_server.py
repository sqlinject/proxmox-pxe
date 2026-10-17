"""Minimal PXE server for Windows: proxyDHCP (67/4011) + TFTP (69) + HTTP (8080).

Works alongside the router's DHCP server: we never hand out addresses, only boot info.
Firmware PXE gets iPXE over TFTP; iPXE then fetches boot.ipxe, kernel and initrd over HTTP.

Usage: python pxe_server.py [server_ip]
"""
import os
import socket
import struct
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.201"
HTTP_PORT = 8080
MAGIC = b"\x63\x82\x53\x63"


def log(*args):
    print(*args, flush=True)


# ---------------------------------------------------------------- proxyDHCP

def parse_options(data):
    opts, i = {}, 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        length = data[i + 1]
        opts[code] = data[i + 2:i + 2 + length]
        i += 2 + length
    return opts


def boot_file(opts):
    if opts.get(77, b"").startswith(b"iPXE"):
        return f"http://{SERVER_IP}:{HTTP_PORT}/boot.ipxe"
    arch = struct.unpack("!H", opts[93][:2])[0] if 93 in opts else 0
    return "undionly.kpxe" if arch == 0 else "snponly.efi"


def build_reply(req, opts, msg_type):
    filename = boot_file(opts)
    reply = bytearray(240)
    reply[0] = 2                      # BOOTREPLY
    reply[1:3] = req[1:3]             # htype, hlen
    reply[4:8] = req[4:8]             # xid
    reply[10:12] = req[10:12]         # flags
    reply[20:24] = socket.inet_aton(SERVER_IP)   # siaddr / next-server
    reply[28:44] = req[28:44]         # chaddr
    reply[44:108] = SERVER_IP.encode().ljust(64, b"\0")
    reply[108:236] = filename.encode().ljust(128, b"\0")
    reply[236:240] = MAGIC

    def opt(code, value):
        reply.extend(bytes([code, len(value)]) + value)

    opt(53, bytes([msg_type]))
    opt(54, socket.inet_aton(SERVER_IP))
    opt(60, b"PXEClient")
    if 97 in opts:
        opt(97, opts[97])
    # PXE vendor options: discovery control = use boot file name, skip boot server discovery
    opt(43, b"\x06\x01\x08\xff")
    opt(66, SERVER_IP.encode())
    opt(67, filename.encode())
    reply.append(255)
    return bytes(reply), filename


def dhcp_loop(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((SERVER_IP, port))
    log(f"proxyDHCP listening on {SERVER_IP}:{port}")
    while True:
        data, addr = sock.recvfrom(4096)
        if len(data) < 240 or data[0] != 1 or data[236:240] != MAGIC:
            continue
        opts = parse_options(data)
        if not opts.get(60, b"").startswith(b"PXEClient"):
            continue
        msg = opts.get(53, b"\0")[0]
        mac = data[28:34].hex(":")
        if port == 67 and msg == 1:          # DISCOVER -> OFFER
            reply, fn = build_reply(data, opts, 2)
            sock.sendto(reply, ("255.255.255.255", 68))
        elif msg in (1, 3):                  # REQUEST/DISCOVER on 4011 (or 67) -> ACK
            reply, fn = build_reply(data, opts, 5)
            dest = addr if port == 4011 else ("255.255.255.255", 68)
            sock.sendto(reply, dest)
        else:
            continue
        log(f"[dhcp:{port}] {mac} msg={msg} -> {fn}")


# --------------------------------------------------------------------- TFTP

def tftp_send(client, filename, opts):
    path = os.path.normpath(os.path.join(ROOT, filename.lstrip("/\\")))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SERVER_IP, 0))
    sock.settimeout(3)
    if not path.startswith(ROOT + os.sep) or not os.path.isfile(path):
        sock.sendto(struct.pack("!HH", 5, 1) + b"File not found\0", client)
        log(f"[tftp] {client[0]} {filename}: not found")
        return
    data = open(path, "rb").read()
    blksize = 512
    oack = {}
    if "blksize" in opts:
        blksize = max(8, min(int(opts["blksize"]), 1468))
        oack["blksize"] = str(blksize)
    if "tsize" in opts:
        oack["tsize"] = str(len(data))

    def exchange(packet, expect_block):
        for _ in range(5):
            sock.sendto(packet, client)
            try:
                while True:
                    ack, _ = sock.recvfrom(1024)
                    op, blk = struct.unpack("!HH", ack[:4])
                    if op == 5:
                        return False
                    if op == 4 and blk == expect_block:
                        return True
            except socket.timeout:
                continue
        return False

    if oack:
        packet = struct.pack("!H", 6) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in oack.items())
        if not exchange(packet, 0):
            return
    block = 1
    offset = 0
    while True:
        chunk = data[offset:offset + blksize]
        if not exchange(struct.pack("!HH", 3, block & 0xFFFF) + chunk, block & 0xFFFF):
            log(f"[tftp] {client[0]} {filename}: aborted at block {block}")
            return
        offset += blksize
        block += 1
        if len(chunk) < blksize:
            break
    log(f"[tftp] {client[0]} {filename}: sent {len(data)} bytes")


def tftp_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SERVER_IP, 69))
    log(f"TFTP listening on {SERVER_IP}:69")
    while True:
        data, client = sock.recvfrom(2048)
        if struct.unpack("!H", data[:2])[0] != 1:     # RRQ only
            continue
        parts = data[2:].split(b"\0")
        filename = parts[0].decode(errors="replace")
        opts = {parts[i].decode().lower(): parts[i + 1].decode() for i in range(2, len(parts) - 1, 2)}
        threading.Thread(target=tftp_send, args=(client, filename, opts), daemon=True).start()


# --------------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log(f"[http] {self.client_address[0]} " + fmt % args)


def write_boot_script():
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    script = f"""#!ipxe
menu Proxmox VE 9.2 network install
item gui Install Proxmox VE (Graphical)
item tui Install Proxmox VE (Terminal UI)
choose --default gui --timeout 5000 target || goto gui
goto ${{target}}

:gui
kernel {base}/linux26 ro ramdisk_size=16777216 rw quiet splash=silent initrd=initrd
goto load
:tui
kernel {base}/linux26 ro ramdisk_size=16777216 rw quiet splash=silent proxtui initrd=initrd
:load
echo Downloading 1.7 GB initrd, this takes a while over Wi-Fi...
initrd {base}/initrd
boot
"""
    with open(os.path.join(ROOT, "boot.ipxe"), "w", newline="\n") as f:
        f.write(script)


if __name__ == "__main__":
    write_boot_script()
    for target, args in ((dhcp_loop, (67,)), (dhcp_loop, (4011,)), (tftp_loop, ())):
        threading.Thread(target=target, args=args, daemon=True).start()
    httpd = ThreadingHTTPServer((SERVER_IP, HTTP_PORT), partial(Handler, directory=ROOT))
    log(f"HTTP serving {ROOT} on http://{SERVER_IP}:{HTTP_PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
