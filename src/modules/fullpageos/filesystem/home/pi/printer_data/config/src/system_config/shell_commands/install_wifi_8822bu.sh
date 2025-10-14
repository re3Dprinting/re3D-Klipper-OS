#!/bin/bash
set -euo pipefail

STATUS=/tmp/flash_status.json
LOG=/var/log/flash_once.log
exec >>"$LOG" 2>&1

jstatus(){ # jstatus <state> <progress> <message> [device]
  printf '{"state":"%s","progress":%s,"message":"%s","device":"%s"}\n' \
    "${1:-running}" "${2:-0}" "${3:-}" "${4:-}" > "$STATUS"
}

echo "=== $(date -Is) 8822BU installer: start ==="
if [ -f /var/lib/flip_to_32bit.changed ]; then
  echo "arch flipped this boot; delaying to next boot"
  exit 0
fi

jstatus running 2 "Preparing Wi-Fi driver (8822BU)"
apt-get update -y || true
apt-get install -y --no-install-recommends dkms raspberrypi-kernel-headers build-essential bc || true

# Prefer DKMS 88x2bu if present; else try lwfinger/rtw88 backport
if [ -d /usr/src/rtl88x2bu-5.13.1 ]; then
  jstatus running 8 "Registering DKMS"
  dkms remove rtl88x2bu/5.13.1 --all >/dev/null 2>&1 || true
  dkms add    rtl88x2bu/5.13.1
  jstatus running 25 "Building DKMS module"
  dkms build  rtl88x2bu/5.13.1
  jstatus running 45 "Installing DKMS module"
  dkms install rtl88x2bu/5.13.1
  modprobe 88x2bu || modprobe rtl88x2bu || true
  echo "DKMS 88x2bu installed."
elif [ -d /opt/rtw88-src ]; then
  jstatus running 25 "Building rtw88 backport"
  make -C /opt/rtw88-src clean
  make -C /opt/rtw88-src
  jstatus running 45 "Installing rtw88 backport"
  make -C /opt/rtw88-src install
  depmod -a
  modprobe rtw88_8822bu || modprobe rtw88_usb || true
  echo "Backport rtw88 installed."
else
  jstatus error 5 "No driver source (88x2bu DKMS or rtw88) found on image"
  exit 0
fi

# Stability: disable Wi-Fi power save
install -d /etc/NetworkManager/conf.d
cat >/etc/NetworkManager/conf.d/wifi-powersave-off.conf <<'EOT'
[connection]
wifi.powersave=2
EOT
systemctl try-restart NetworkManager || true

touch /var/lib/firstboot-8822bu.ok
jstatus running 60 "Wi-Fi driver ready"
echo "=== $(date -Is) 8822BU installer: done ==="
exit 0
