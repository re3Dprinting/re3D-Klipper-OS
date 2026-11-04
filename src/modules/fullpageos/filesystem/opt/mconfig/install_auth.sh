#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-live}"
if [[ "$MODE" != "live" && "$MODE" != "image" ]]; then
  echo "Usage: install_auth.sh [live|image]" >&2
  exit 1
fi
if ! command -v nft >/dev/null; then
  apt-get install -y nftables
fi

getent passwd re3d-auth >/dev/null || useradd --system --user-group --home-dir /var/lib/re3d-auth --shell /usr/sbin/nologin re3d-auth
install -d -m 0700 -o re3d-auth -g re3d-auth /var/lib/re3d-auth
install -d -m 0755 -o root -g root /etc/re3d-airgap
for service in NetworkManager systemd-networkd wpa_supplicant wpa_supplicant@ dhcpcd dhcpcd@ networking connman iwd ModemManager bluetooth systemd-rfkill getty@ serial-getty@ console-getty debug-shell re3d-security; do
  install -d -m 0755 "/etc/systemd/system/${service}.service.d"
  printf '[Unit]\nRequires=re3d-airgap.service\nAfter=re3d-airgap.service\n' \
    > "/etc/systemd/system/${service}.service.d/re3d-airgap.conf"
  if [[ "$service" != "re3d-security" ]]; then
    printf 'ConditionPathExists=!/etc/re3d-airgap/enabled\nConditionPathExists=!/run/re3d-airgap/enabled\n' \
      >> "/etc/systemd/system/${service}.service.d/re3d-airgap.conf"
  fi
done
for socket in systemd-networkd systemd-rfkill; do
  install -d -m 0755 "/etc/systemd/system/${socket}.socket.d"
  printf '[Unit]\nRequires=re3d-airgap.service\nAfter=re3d-airgap.service\nConditionPathExists=!/etc/re3d-airgap/enabled\nConditionPathExists=!/run/re3d-airgap/enabled\n' \
    > "/etc/systemd/system/${socket}.socket.d/re3d-airgap.conf"
done
install -d -m 0755 /etc/systemd/system/display-manager.service.d
printf '[Unit]\nRequires=re3d-airgap-helper.service\nAfter=re3d-airgap-helper.service\n' \
  > /etc/systemd/system/display-manager.service.d/re3d-airgap.conf
install -d -m 0755 /etc/nginx/re3d-auth
install -d -m 0755 /etc/systemd/system/nginx.service.d
install -m 0644 /opt/mconfig/nginx/service.conf /etc/systemd/system/nginx.service.d/re3d-auth.conf
for service in moonraker crowsnest mconfig; do
  install -d -m 0755 "/etc/systemd/system/${service}.service.d"
  printf '[Unit]\nRequires=re3d-security.service\nAfter=re3d-security.service\n' \
    > "/etc/systemd/system/${service}.service.d/re3d-security.conf"
done

install -m 0644 /opt/mconfig/nginx/common.conf /etc/nginx/re3d-auth/common.conf
install -m 0644 /opt/mconfig/nginx/mainsail.conf /etc/nginx/re3d-auth/mainsail.conf
install -m 0644 /opt/mconfig/nginx/maps.conf /etc/nginx/conf.d/re3d-auth.conf
install -m 0644 /opt/mconfig/nginx/configurator.conf /etc/nginx/sites-available/re3d-configurator

python3 - <<'PY'
from pathlib import Path
import re
import shutil

site = Path("/etc/nginx/sites-available/mainsail")
text = site.read_text()
include = "    include /etc/nginx/re3d-auth/mainsail.conf;"
if include not in text:
    if len(re.findall(r"(?m)^\s*server\s*\{", text)) != 1:
        raise RuntimeError("Expected one Mainsail server block; integrate re3d-auth/mainsail.conf manually")
    backup = site.with_name("mainsail.pre-auth")
    if not backup.exists():
        shutil.copy2(site, backup)
    text = re.sub(r"(?m)^(\s*server\s*\{)", lambda match: match[0] + "\n" + include, text, count=1)
    site.write_text(text)
PY

ln -sf /etc/nginx/sites-available/re3d-configurator /etc/nginx/sites-enabled/re3d-configurator
systemctl enable re3d-security.service re3d-auth.service re3d-auth-tls.service re3d-auth-revoke.path re3d-ssh-password.service
systemctl enable re3d-airgap.service re3d-airgap-helper.service
if [[ "$MODE" == "live" ]]; then
  bash /opt/mconfig/prepare_auth_tls.sh
  nginx -t
  systemctl daemon-reload
  # Updates run inside a BusyBox CGI process. Restarting its service kills the updater.
  echo "HTTPS authentication configured; listeners will switch together at the update reboot."
fi
