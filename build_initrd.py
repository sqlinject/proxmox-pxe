"""Append the Proxmox ISO to the stock initrd as an extra (uncompressed) newc cpio archive.

The kernel unpacks concatenated initramfs archives in order, so the result is the
original initrd plus /proxmox.iso, which the Proxmox init script looks for.

Usage: python build_initrd.py <path-to-proxmox-ve.iso>
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) != 2:
    sys.exit("usage: python build_initrd.py <path-to-proxmox-ve.iso>")
ISO = sys.argv[1]
SRC = os.path.join(HERE, "initrd.orig")
DST = os.path.join(HERE, "initrd")


def newc_header(name, size, mode, ino):
    namesize = len(name) + 1
    fields = [ino, mode, 0, 0, 1, 0, size, 0, 0, 0, 0, namesize, 0]
    return b"070701" + b"".join(b"%08X" % f for f in fields) + name.encode() + b"\0"


def pad(f):
    f.write(b"\0" * (-f.tell() % 4))


iso_size = os.path.getsize(ISO)
with open(DST, "wb") as out:
    with open(SRC, "rb") as src:
        shutil.copyfileobj(src, out)
    pad(out)
    out.write(newc_header("proxmox.iso", iso_size, 0o100644, 1))
    pad(out)
    with open(ISO, "rb") as iso:
        shutil.copyfileobj(iso, out, 16 * 1024 * 1024)
    pad(out)
    out.write(newc_header("TRAILER!!!", 0, 0, 0))
    pad(out)

print(f"wrote {DST} ({os.path.getsize(DST):,} bytes)")
