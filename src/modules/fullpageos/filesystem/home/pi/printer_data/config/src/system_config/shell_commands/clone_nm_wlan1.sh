#!/bin/bash
set -euo pipefail

SRC=/boot/firmware/network-config
[ -f /boot/network-config ] && SRC=/boot/network-config   # optional fallback

DST=/etc/NetworkManager/system-connections/wifi-wlan1.nmconnection
LOG=/var/log/clone_nm_wlan1.log
exec >>"$LOG" 2>&1

read_kv() {
  # very simple netplan-ish YAML grab; good enough for Imager payload
  local key="$1"
  grep -E "^\s*${key}:" "$SRC" | head -1 | awk -F':' '{print $2}' | tr -d ' "' || true
}

if [ -f "$SRC" ]; then
  # Imager writes:
  # access-points:
  #   "SSID":
  #     password: "PSK"
  ssid=$(grep -A2 "access-points" "$SRC" | grep -oP '"\K[^"]+' | head -1 || true)
  psk=$(grep -A5 "access-points" "$SRC" | grep -E "password:" | head -1 | awk -F':' '{print $2}' | tr -d ' "' || true)

  if [ -n "${ssid:-}" ] && [ -n "${psk:-}" ]; then
    tmp=$(mktemp)
    cat >"$tmp" <<EOF
[connection]
id=wifi-wlan1
type=wifi
interface-name=wlan1
autoconnect=true
autoconnect-priority=10

[wifi]
ssid=$ssid
mode=infrastructure

[wifi-security]
key-mgmt=wpa-psk
psk=$psk

[ipv4]
method=auto
route-metric=100

[ipv6]
method=ignore
EOF

    # Only replace if content changed
    if [ ! -f "$DST" ] || ! cmp -s "$tmp" "$DST"; then
      install -m 600 -o root -g root "$tmp" "$DST"
      nmcli connection reload
      echo "Created/updated $DST for SSID '$ssid'"
    else
      echo "No changes to $DST"
    fi
    rm -f "$tmp"
  else
    echo "No SSID/PSK found in $SRC"
  fi
else
  echo "$SRC not found"
fi
