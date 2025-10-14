#!/bin/bash
set -e
SRC=/boot/firmware/network-config
DST=/etc/NetworkManager/system-connections/wifi-wlan1.nmconnection
LOG=/var/log/clone_nm_wlan1.log
exec >>"$LOG" 2>&1

if [ -f "$SRC" ]; then
  ssid=$(grep -A2 "access-points" "$SRC" | grep -oP '"\K[^"]+')
  psk=$(grep "password:" "$SRC" | awk '{print $2}' | tr -d '"')

  if [ -n "$ssid" ] && [ -n "$psk" ]; then
    cat >"$DST" <<EOF
[connection]
id=wifi-wlan1
type=wifi
interface-name=wlan1
autoconnect=true

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
    chmod 600 "$DST"
    nmcli connection reload
    echo "Created $DST for SSID $ssid"
  else
    echo "No SSID/PSK found in $SRC"
  fi
else
  echo "$SRC not found"
fi
