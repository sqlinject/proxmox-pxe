# Proxmox VE PXE Server for Windows

Install [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment) over the network from an ordinary Windows PC, with no USB stick, no extra software and no changes to your router.

`pxe_server.py` is a single-file, standard-library Python PXE server. It runs **alongside** your existing router's DHCP as a *proxyDHCP*: the router still hands out IP addresses, and this server only tells PXE clients what to boot.

## How it works

```
 Target machine                Router (DHCP)          This PC (pxe_server.py)
 ──────────────                ─────────────          ───────────────────────
 PXE firmware ── DHCPDISCOVER ──►  IP address
              ◄──────────────────────────────────────  proxyDHCP: "boot snponly.efi / undionly.kpxe"
              ── TFTP ───────────────────────────────►  iPXE binary
 iPXE         ── DHCP again ─────────────────────────►  proxyDHCP: "boot http://<ip>:8080/boot.ipxe"
              ── HTTP ───────────────────────────────►  boot.ipxe menu → linux26 + initrd (ISO inside)
 Proxmox installer starts
```

| Port | Protocol | Purpose |
|------|----------|---------|
| 67, 4011 | UDP | proxyDHCP (boot info only, never IP addresses) |
| 69 | UDP | TFTP, serves the iPXE binary to the firmware |
| 8080 | TCP | HTTP, serves the boot menu, kernel and initrd (much faster than TFTP) |

The Proxmox installer expects to find its ISO, so `build_initrd.py` appends the whole ISO to the stock initrd as an extra cpio archive. The kernel unpacks it to `/proxmox.iso` at boot. Community tools such as [pve-iso-2-pxe](https://github.com/morph027/pve-iso-2-pxe) use the same trick.

## Files

| File | Description |
|------|-------------|
| `pxe_server.py` | proxyDHCP + TFTP + HTTP server. Writes `boot.ipxe` on startup. |
| `build_initrd.py` | Builds `initrd` = `initrd.orig` + the Proxmox ISO. |
| `allow_firewall.ps1` | Allows Python through Windows Firewall for the ports above. |

The binaries (kernel, initrd, iPXE) are **not** in the repo. You download or extract them in the steps below.

## Requirements

- Windows 10/11 with Python 3.8+ ([python.org](https://www.python.org/downloads/)), using the standard library only
- A Proxmox VE ISO ([download](https://www.proxmox.com/en/downloads)). Tested with **9.2**.
- The PXE server and the target machine on the same LAN / broadcast domain
- A target machine with **plenty of RAM** (8 GB+ recommended), because the whole ISO is loaded into memory
- A wired connection if you can get one. The initrd is ~1.7 GB, which is slow over Wi-Fi.

## Setup

**1. Clone the repo**

```powershell
git clone https://github.com/c-dunc/proxmox-pxe.git
cd proxmox-pxe
```

**2. Download iPXE**

```powershell
curl.exe -LO https://boot.ipxe.org/undionly.kpxe                # legacy BIOS
curl.exe -LO https://boot.ipxe.org/x86_64-efi/snponly.efi       # UEFI
```

**3. Extract the kernel and initrd from the ISO.** Double-click the ISO to mount it (say as `E:`), then:

```powershell
copy E:\boot\linux26 .
copy E:\boot\initrd.img initrd.orig
```

**4. Build the combined initrd**

```powershell
python build_initrd.py C:\path\to\proxmox-ve_9.2-1.iso
```

**5. Open the firewall** (elevated PowerShell, run once):

```powershell
powershell -ExecutionPolicy Bypass -File .\allow_firewall.ps1
```

Your folder should now contain `pxe_server.py`, `linux26`, `initrd.orig`, `initrd`, `snponly.efi` and `undionly.kpxe`.

## Usage

Find your PC's LAN IP (`ipconfig`), then start the server bound to it:

```powershell
python pxe_server.py 192.168.1.50
```

On the target machine, enable network/PXE boot in the BIOS/UEFI and choose it from the boot menu (often F12). You'll get a menu:

- **Install Proxmox VE (Graphical)**, the default after 5 seconds
- **Install Proxmox VE (Terminal UI)**

Example output of a successful UEFI boot:

```
[dhcp:67] aa:bb:cc:dd:ee:ff msg=1 -> snponly.efi
[tftp] 192.168.1.60 snponly.efi: sent 305664 bytes
[dhcp:67] aa:bb:cc:dd:ee:ff msg=1 -> http://192.168.1.50:8080/boot.ipxe
[http] 192.168.1.60 "GET /boot.ipxe HTTP/1.1" 200 -
[http] 192.168.1.60 "GET /linux26 HTTP/1.1" 200 -
[http] 192.168.1.60 "GET /initrd HTTP/1.1" 200 -
```

Press `Ctrl+C` to stop the server.

## Troubleshooting

- **Nothing appears in the log when the target boots.** Windows Firewall is the usual cause. Re-run `allow_firewall.ps1`, and check that your network is not set to a profile that blocks inbound traffic. Also make sure you passed the IP of the interface that is on the same LAN as the target.
- **"Address already in use" on port 67 or 69.** Another DHCP/TFTP service is running (e.g. Tftpd64, Windows DHCP Server, WDS). Stop it first.
- **`autoexec.ipxe: not found` in the log.** This is harmless. iPXE looks for it first, then asks DHCP again and gets `boot.ipxe`.
- **Installer fails with "no cdrom found" / ISO not found.** `initrd` wasn't rebuilt or isn't the one being served. Re-run `build_initrd.py`.
- **Kernel panic / out of memory early in boot.** The target doesn't have enough RAM to hold the ~1.7 GB initrd and unpack it.
- **Secure Boot.** The stock iPXE binaries are unsigned. Disable Secure Boot on the target for the install.

## Cleaning up

Stop the server, then remove the firewall rules (elevated PowerShell):

```powershell
Get-NetFirewallRule -DisplayName 'PXE server*' | Remove-NetFirewallRule
```

You can also delete `initrd`, which is ~1.7 GB. It can be rebuilt at any time.

## Security notes

- The server answers **any** PXE client on the LAN and serves every file in its folder over HTTP and TFTP, without authentication. Only run it on a network you trust, and only while you need it.
- Do not run it on a network that already has a PXE/proxyDHCP server, or clients may get conflicting answers.

## Adapting it

Nothing here is specific to Proxmox except the menu in `write_boot_script()` in `pxe_server.py`. Swap in another kernel/initrd and edit the iPXE script to boot something else.
