#!/usr/bin/env bash
# wifi-both.sh / set_wifi.sh
# - CLI: sudo ./set_wifi.sh SSID PSK
# - CGI: POST with form fields "ssid" and "psk"
# Use "-" as PSK for open networks.

set -eo pipefail

MODE="cli"
[[ -n "${REQUEST_METHOD:-}" ]] && MODE="cgi"

echo_header=0
http_header() {
  if (( echo_header == 0 )); then
    echo "Content-Type: text/plain"
    echo
    echo_header=1
  fi
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    [[ "$MODE" == "cgi" ]] && http_header
    echo "Missing: $1"
    exit 1
  fi
}

read_stdin() {
  local len="${CONTENT_LENGTH:-0}"
  local data=""
  if [[ "$len" -gt 0 ]]; then
    # bash-specific: read exact length
    IFS= read -r -N "$len" data 2>/dev/null || true
  fi
  printf '%s' "$data"
}

urldecode() {
  local data="$1"
  data="${data//+/ }"
  printf '%b' "${data//%/\\x}"
}

extract_field() {
  local name="$1"
  local src="$2"
  local val
  # split on & then grab the line starting with name=
  val=$(printf '%s' "$src" | tr '&' '\n' | sed -n "s/^${name}=//p" | head -n1)
  printf '%s' "$val"
}

SSID=""
PSK=""

if [[ "$MODE" == "cgi" ]]; then
  http_header

  BODY="$(read_stdin)"

  raw_ssid="$(extract_field "ssid" "$BODY")"
  raw_psk="$(extract_field "psk"  "$BODY")"

  SSID="$(urldecode "$raw_ssid")"
  PSK="$(urldecode "$raw_psk")"

  if [[ -z "$SSID" ]]; then
    echo "Error: Missing ssid"
    exit 2
  fi
  # PSK may be empty (open network) or "-" to mean open
else
  if [[ $# -lt 2 ]]; then
    echo "Usage: sudo $0 SSID PSK"
    echo "       (Use '-' for PSK on open networks)"
    exit 2
  fi
  SSID="$1"
  PSK="$2"
fi

[[ "$PSK" == "-" ]] && PSK=""

need nmcli

dev_exists() { nmcli -t -f DEVICE device | grep -Fxq "$1"; }

# Delete any Wi-Fi profiles pinned to this interface (prevents autoconnect to stale SSIDs)
delete_iface_wifi_profiles() {
  local IFACE="$1"
  nmcli -t -f NAME connection show | while IFS= read -r CNAME; do
    [[ -z "$CNAME" ]] && continue
    local TYP IFB
    TYP="$(nmcli -g connection.type connection show "$CNAME" 2>/dev/null || true)"
    IFB="$(nmcli -g connection.interface-name connection show "$CNAME" 2>/dev/null || true)"
    if [[ "$TYP" = "802-11-wireless" && "$IFB" = "$IFACE" ]]; then
      nmcli -t connection delete "$CNAME" >/dev/null 2>&1 || true
    fi
  done
}

make_and_up() {
  local IFACE="$1"
  local NAME="wifi-$IFACE"

  echo "---- $IFACE: disconnect & clean ----"
  nmcli device disconnect "$IFACE" >/dev/null 2>&1 || true
  delete_iface_wifi_profiles "$IFACE"
  nmcli device wifi rescan ifname "$IFACE" || true
  sleep 1

  echo "---- $IFACE: create fresh profile '$NAME' for '$SSID' ----"
  nmcli connection add type wifi ifname "$IFACE" con-name "$NAME" ssid "$SSID" >/dev/null

  nmcli connection modify "$NAME" \
    connection.interface-name "$IFACE" \
    802-11-wireless.cloned-mac-address permanent \
    connection.autoconnect yes \
    ipv4.method auto \
    ipv6.method ignore

  if [[ -z "$PSK" ]]; then
    nmcli connection modify "$NAME" wifi-sec.key-mgmt none
  else
    nmcli connection modify "$NAME" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PSK"
  fi

  echo "---- $IFACE: bring up (pass 1) ----"
  if nmcli connection up "$NAME" ifname "$IFACE"; then
    echo "✅ $IFACE: connected to '$SSID' (profile '$NAME')"
    return 0
  fi

  # If PSK provided and WPA2 failed, try WPA3/SAE
  if [[ -n "$PSK" ]]; then
    echo ".... $IFACE: WPA2-PSK failed; retrying WPA3/SAE"
    nmcli connection modify "$NAME" wifi-sec.key-mgmt sae wifi-sec.psk "$PSK"
    if nmcli connection up "$NAME" ifname "$IFACE"; then
      echo "✅ $IFACE: connected to '$SSID' via WPA3/SAE (profile '$NAME')"
      return 0
    fi
  fi

  echo "❌ $IFACE: failed to connect to '$SSID'"
  return 1
}

rc=0
for iface in wlan0 wlan1; do
  if dev_exists "$iface"; then
    make_and_up "$iface" || rc=1
  else
    echo "Skipping $iface: device not found."
  fi
done

exit "$rc"
