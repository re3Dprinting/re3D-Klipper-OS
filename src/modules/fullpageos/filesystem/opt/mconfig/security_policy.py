#!/usr/bin/env python3
"""Root-only boot policy. Never accepts paths or commands from HTTP clients."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import shutil

from auth_server import AuthState


STATE = Path("/var/lib/re3d-auth/password.json")
RUNTIME = Path("/run/re3d-security")
MARKER = Path("/etc/re3d-security/lockdown")
SSH_CONFIG = Path("/etc/re3d-security/sshd_config")
SSH_UNIT = Path("/etc/systemd/system/ssh.service.d/re3d-lockdown.conf")
SSH_RUNTIME = Path("/run/sshd")
SERVICE_BACKUP = Path("/var/lib/re3d-security/services.json")
DESKTOP_SERVICES = ("x11vnc.service", "wayvnc.service", "vncserver-x11-serviced.service",
                    "KlipperScreen.service")
CHROME_POLICIES = (Path("/etc/chromium/policies/managed/re3d-lockdown.json"),
                   Path("/etc/chromium-browser/policies/managed/re3d-lockdown.json"))
SSH_OPTIONS = """# Managed by re3D security mode; restored on leaving Lockdown.
Port 22
UsePAM yes
StrictModes yes
AuthorizedKeysFile /home/pi/.ssh/authorized_keys
AllowUsers pi
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
DisableForwarding yes
X11Forwarding no
PermitTunnel no
Subsystem sftp internal-sftp
"""


def run(*args, check=True, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=30,
                          env={**os.environ, "LC_ALL": "C"})


def ssh_readiness():
    try:
        if not shutil.which("cage") or not shutil.which("nft"):
            raise ValueError("Lockdown requires cage and nftables to be installed")
        for action, expected in (("is-enabled", "enabled"), ("is-active", "active")):
            if run("systemctl", action, "ssh.socket", check=False).stdout.strip() == expected:
                raise ValueError("Disable SSH socket activation before Lockdown; use ssh.service instead")
        if run("systemctl", "is-enabled", "ssh.service", check=False).stdout.strip() != "enabled":
            raise ValueError("Enable ssh.service and test SSH key login before Lockdown")
        uid = int(run("id", "-u", "pi").stdout)
        for path in (Path("/home/pi"), Path("/home/pi/.ssh"), Path("/home/pi/.ssh/authorized_keys")):
            info = path.stat()
            if info.st_uid not in (0, uid) or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ValueError(f"{path} must be owned by pi/root and not group/world writable")
        result = run("ssh-keygen", "-l", "-f", "/home/pi/.ssh/authorized_keys")
        if not result.stdout.strip():
            raise ValueError("No valid SSH recovery key found")
        options = ("-f", str(SSH_CONFIG)) if SSH_CONFIG.exists() else ()
        run("/usr/sbin/sshd", "-t", *options)
        return {"ready": True}
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        return {"ready": False, "error": f"Configure and test pi's SSH recovery key first: {error}"}


def firewall_rules(quarantine=False, replace=False):
    ports = "" if quarantine else "tcp dport { 22, 80, 443, 8081, 8443 } accept;"
    prefix = "delete table inet re3d_lockdown;\n" if replace else ""
    return prefix + """table inet re3d_lockdown {
    chain input {
        type filter hook input priority -10; policy drop;
        iifname "lo" accept;
        ct state invalid drop;
        ct state established,related accept;
        ip protocol icmp accept;
        meta l4proto ipv6-icmp accept;
        udp sport 67 udp dport 68 accept;
        udp sport 547 udp dport 546 accept;
        ip daddr 224.0.0.251 udp dport 5353 accept;
        ip6 daddr ff02::fb udp dport 5353 accept;
        """ + ports + """
    }
}
"""


def table_exists():
    result = run("nft", "list", "table", "inet", "re3d_lockdown", check=False)
    if result.returncode == 0:
        return True
    if "No such file or directory" in result.stderr:
        return False
    raise RuntimeError(f"Cannot inspect lockdown firewall: {result.stderr}")


def set_firewall(quarantine=False):
    run("nft", "-f", "-", input=firewall_rules(quarantine, table_exists()))


def write(path, text, public=True):
    missing = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755 if public else 0o700)
        directory.chmod(0o755 if public else 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o644 if public else 0o600)
    os.replace(temporary, path)


def stop_desktops():
    if not SERVICE_BACKUP.exists():
        services = {}
        for name in DESKTOP_SERVICES:
            result = run("systemctl", "show", "-p", "LoadState", "--value", name, check=False)
            loaded = result.stdout.strip()
            if loaded == "not-found":
                continue
            result.check_returncode()
            enabled = run("systemctl", "is-enabled", name, check=False).stdout.strip()
            active = run("systemctl", "is-active", name, check=False).stdout.strip()
            services[name] = {"enabled": enabled == "enabled", "active": active == "active",
                              "masked": enabled == "masked"}
        write(SERVICE_BACKUP, json.dumps(services), public=False)
    for name, previous in json.loads(SERVICE_BACKUP.read_text()).items():
        if not previous["masked"]:
            run("systemctl", "disable", name)
        run("systemctl", "stop", "--no-block", name)


def restore_desktops():
    if SERVICE_BACKUP.exists():
        for name, previous in json.loads(SERVICE_BACKUP.read_text()).items():
            if previous["enabled"]:
                run("systemctl", "enable", name)
            if previous["active"] or previous["enabled"]:
                run("systemctl", "start", "--no-block", name)
        SERVICE_BACKUP.unlink()


def apply(mode):
    if mode == "lockdown":
        # Until prerequisites and the SSH effective configuration are verified, expose no TCP services.
        set_firewall(quarantine=True)
        SSH_RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o755)
        SSH_RUNTIME.chmod(0o755)
        write(SSH_CONFIG, SSH_OPTIONS)
        result = ssh_readiness()
        if not result["ready"]:
            raise RuntimeError(result["error"])
        effective = run("/usr/sbin/sshd", "-T", "-f", str(SSH_CONFIG)).stdout.splitlines()
        for option in ("passwordauthentication no", "kbdinteractiveauthentication no",
                       "authenticationmethods publickey", "pubkeyauthentication yes",
                       "permitrootlogin no", "disableforwarding yes", "allowusers pi"):
            if option not in effective:
                raise RuntimeError(f"SSH override prevents Lockdown: expected {option}")
        if "authorizedkeysfile /home/pi/.ssh/authorized_keys" not in effective:
            raise RuntimeError("SSH must use pi's checked .ssh/authorized_keys recovery file")
        write(SSH_UNIT, "[Service]\nExecStartPre=\n"
              f"ExecStartPre=/usr/sbin/sshd -t -f {SSH_CONFIG}\nExecStart=\n"
              f"ExecStart=/usr/sbin/sshd -D -f {SSH_CONFIG}\nExecReload=\n"
              f"ExecReload=/usr/sbin/sshd -t -f {SSH_CONFIG}\n"
              "ExecReload=/bin/kill -HUP $MAINPID\n")
        run("systemctl", "daemon-reload")
        command = run("systemctl", "show", "-p", "ExecStart", "--value", "ssh.service").stdout
        if f"argv[]=/usr/sbin/sshd -D -f {SSH_CONFIG} ;" not in command:
            raise RuntimeError("An SSH service override prevents the key-only Lockdown command")
        stop_desktops()
        policies = {
            "DeveloperToolsAvailability": 2, "BrowserGuestModeEnabled": False,
            "BrowserAddPersonEnabled": False, "IncognitoModeAvailability": 1,
            "PasswordManagerEnabled": False, "PrintingEnabled": False,
            "DownloadRestrictions": 3, "AllowFileSelectionDialogs": False,
            "URLBlocklist": ["*"],
            "URLAllowlist": ["http://localhost:80/*", "http://localhost:8081/*"],
        }
        for path in CHROME_POLICIES:
            write(path, json.dumps(policies))
        write(MARKER, "lockdown\n")
        set_firewall()
    else:
        if mode not in ("off", "basic"):
            raise ValueError("Invalid security mode")
        if table_exists():
            run("nft", "delete", "table", "inet", "re3d_lockdown")
        if SSH_CONFIG.exists() or SSH_UNIT.exists():
            SSH_CONFIG.unlink(missing_ok=True)
            SSH_UNIT.unlink(missing_ok=True)
            run("/usr/sbin/sshd", "-t")
            run("systemctl", "daemon-reload")
        for path in CHROME_POLICIES:
            path.unlink(missing_ok=True)
        MARKER.unlink(missing_ok=True)
        restore_desktops()
    write(RUNTIME / "mode", mode + "\n")


def main():
    if sys.argv[1:] == ["--check"]:
        print(json.dumps(ssh_readiness()))
    elif not sys.argv[1:]:
        try:
            state = AuthState(STATE)
        except (OSError, ValueError):
            set_firewall(quarantine=True)
            raise
        apply(state.mode)
    else:
        raise ValueError("Only --check or boot-time policy application is supported")


if __name__ == "__main__":
    main()
