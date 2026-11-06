#!/usr/bin/env python3
"""Boot-time isolation and a restricted, local settings helper."""

import errno
import json
import logging
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile


ENABLED = Path("/etc/re3d-airgap/enabled")
APPLIED = Path("/run/re3d-airgap/enabled")
SOCKET = "/run/re3d-airgap/settings.sock"
AUTH_STATE = Path("/var/lib/re3d-auth/password.json")
RULES = Path("/run/udev/rules.d/99-re3d-airgap.rules")
RADIOS = Path("/etc/re3d-airgap/radios.json")
NETWORK_UNITS = (
    "NetworkManager.service", "systemd-networkd.service", "wpa_supplicant.service",
    "wpa_supplicant@.service", "dhcpcd.service", "dhcpcd@.service",
    "networking.service", "connman.service", "iwd.service", "ModemManager.service",
    "bluetooth.service", "systemd-networkd.socket", "systemd-rfkill.service", "systemd-rfkill.socket",
)
CONSOLE_UNITS = ("getty@.service", "serial-getty@.service", "console-getty.service",
                 "debug-shell.service")


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True,
                          check=True, timeout=30)


def save(enabled):
    if type(enabled) is not bool:
        raise ValueError("Air-gap enabled must be a boolean")
    ENABLED.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        if not ENABLED.exists() and not APPLIED.exists() and not RADIOS.exists():
            atomic_write(RADIOS, json.dumps(radio_states()))
        atomic_write(ENABLED, "enabled\n")
    else:
        ENABLED.unlink(missing_ok=True)
        if not APPLIED.exists():
            RADIOS.unlink(missing_ok=True)
        sync_directory(ENABLED.parent)


def sync_directory(path):
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def firewall_rules(replace=False):
    prefix = "delete table inet re3d_airgap;\n" if replace else ""
    return prefix + """table inet re3d_airgap {
    chain input {
        type filter hook input priority -100; policy drop;
        iifname "lo" accept;
    }
    chain output {
        type filter hook output priority -100; policy drop;
        oifname "lo" accept;
    }
    chain forward {
        type filter hook forward priority -100; policy drop;
    }
}
"""


def disable_link(name):
    if name == "lo":
        return
    if not name or "/" in name or name.startswith("-"):
        raise ValueError("Invalid network interface")
    path = Path("/sys/class/net") / name
    try:
        flags = int((path / "flags").read_text().strip(), 16)
    except FileNotFoundError:
        return  # A removable adapter can disappear between enumeration and inspection.
    if flags & 1:
        try:
            run("ip", "link", "set", "dev", name, "down")
        except subprocess.CalledProcessError:
            if path.exists():
                raise


def disable_links():
    for path in Path("/sys/class/net").iterdir():
        disable_link(path.name)


def radio_states():
    return {(path / "name").read_text().strip(): int((path / "soft").read_text().strip())
            for path in Path("/sys/class/rfkill").glob("rfkill*")}


def set_radio(index=0, kind=0, operation=3, blocked=1):
    device = Path("/dev/rfkill")
    if device.exists():
        with device.open("wb", buffering=0) as output:
            output.write(struct.pack("IBBBB", index, kind, operation, blocked, 0))
    elif any(Path("/sys/class/rfkill").glob("rfkill*")):
        raise RuntimeError("Radio devices exist but /dev/rfkill is unavailable")


def block_radios():
    # RFKILL_OP_CHANGE_ALL also sets the default for subsequently attached radios.
    set_radio()


def restore_radios():
    if not RADIOS.exists():
        return
    states = json.loads(RADIOS.read_text())
    set_radio(blocked=0)
    for path in Path("/sys/class/rfkill").glob("rfkill*"):
        name = (path / "name").read_text().strip()
        if name in states:
            set_radio(index=int(path.name.removeprefix("rfkill")), operation=2, blocked=states[name])
    RADIOS.unlink()
    sync_directory(RADIOS.parent)


def require_pin():
    from auth_server import AuthState

    if not AuthState(AUTH_STATE).has_pin:
        raise ValueError("Air-gapped mode requires a touchscreen PIN. Configure it before rebooting.")


def apply():
    if not ENABLED.exists():
        restore_radios()
        return
    if not RADIOS.exists():
        atomic_write(RADIOS, json.dumps(radio_states()))
    tables = json.loads(run("nft", "-j", "list", "tables").stdout)
    replace = any(item.get("table", {}).get("family") == "inet"
                  and item["table"].get("name") == "re3d_airgap"
                  for item in tables["nftables"])
    run("nft", "-f", "-", input=firewall_rules(replace))
    require_pin()
    # Keep all connection managers and console keyboard paths disabled for this boot only.
    run("systemctl", "mask", "--runtime", *NETWORK_UNITS, *CONSOLE_UNITS)
    block_radios()
    disable_links()
    run("sysctl", "-w", "kernel.sysrq=0")
    atomic_write(RULES, '''SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", ENV{ID_INPUT_TOUCHSCREEN}!="1", ENV{LIBINPUT_IGNORE_DEVICE}="1", OWNER="root", GROUP="root", MODE="0000"
ACTION=="add", SUBSYSTEM=="net", KERNEL!="lo", RUN+="/usr/bin/python3 /opt/mconfig/airgap.py --link %k"
''')
    run("udevadm", "control", "--reload-rules")
    run("udevadm", "trigger", "--action=change", "--subsystem-match=input")
    run("udevadm", "settle", "--timeout=15")
    atomic_write(APPLIED, "enabled\n")


def keyboard_device(properties):
    return properties.get("ID_INPUT_KEYBOARD") == "1" and properties.get("ID_INPUT_TOUCHSCREEN") != "1"


class Keyboards:
    def __init__(self):
        self.grabbed = {}

    def scan(self):
        import fcntl

        devices = set(Path("/dev/input").glob("event*"))
        for path in list(self.grabbed):
            try:
                replaced = path not in devices or os.stat(path).st_ino != os.fstat(self.grabbed[path]).st_ino
            except FileNotFoundError:
                replaced = True
            if replaced:
                os.close(self.grabbed.pop(path))
        for path in devices - self.grabbed.keys():
            try:
                result = run("udevadm", "info", "--query=property", "--name", str(path))
                properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                if not keyboard_device(properties):
                    continue
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                try:
                    # EVIOCGRAB stops keyboard events reaching other readers, including virtual consoles.
                    fcntl.ioctl(descriptor, 0x40044590, 1)
                except OSError:
                    os.close(descriptor)
                    raise
                self.grabbed[path] = descriptor
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ENODEV):
                    raise
            except subprocess.CalledProcessError:
                if path.exists():
                    raise

    def close(self):
        for descriptor in self.grabbed.values():
            os.close(descriptor)
        self.grabbed.clear()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            line = self.rfile.readline(1025)
            if len(line) > 1024 or not line.endswith(b"\n"):
                raise ValueError("Invalid air-gap request size")
            data = json.loads(line)
            if not isinstance(data, dict) or set(data) != {"enabled"}:
                raise ValueError("Expected only the air-gap enabled setting")
            save(data["enabled"])
            body = {"status": 200, "ok": True, "enabled": ENABLED.exists(),
                    "applied": APPLIED.exists(), "reboot_required": ENABLED.exists() != APPLIED.exists()}
        except (ValueError, UnicodeError) as error:
            body = {"status": 400, "error": str(error)}
        except OSError:
            logging.exception("Air-gap settings could not be saved")
            body = {"status": 500, "error": "Could not save air-gap settings. Check re3d-airgap-helper.service logs."}
        self.wfile.write(json.dumps(body).encode() + b"\n")


def serve():
    Path(SOCKET).unlink(missing_ok=True)
    os.umask(0o117)
    keyboards = Keyboards()
    try:
        with socketserver.UnixStreamServer(SOCKET, Handler) as server:
            server.timeout = 0.5
            if APPLIED.exists():
                keyboards.scan()
            address = os.environ.get("NOTIFY_SOCKET")
            if address:
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                    notify.connect("\0" + address[1:] if address.startswith("@") else address)
                    notify.sendall(b"READY=1")
            while True:
                server.handle_request()
                if APPLIED.exists():
                    block_radios()
                    disable_links()
                    keyboards.scan()
    finally:
        keyboards.close()


def main():
    if sys.argv[1:] == ["--apply"]:
        apply()
    elif sys.argv[1:] == ["--serve"]:
        serve()
    elif len(sys.argv) == 3 and sys.argv[1] == "--link":
        if ENABLED.exists() or APPLIED.exists():
            disable_link(sys.argv[2])
    else:
        raise ValueError("Use --apply, --serve, or --link INTERFACE")


if __name__ == "__main__":
    main()
