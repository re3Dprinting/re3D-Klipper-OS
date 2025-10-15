#!/usr/bin/env bash
# wifi-both.sh — connect 1+ interfaces to an SSID using args (no prompts)
# Example:
#   sudo ./wifi-both.sh --ssid "re3D" --psk "SuperSecret"
#   sudo ./wifi-both.sh --ssid "MyOpenNet" --open
#   sudo ./wifi-both.sh --ssid "CorpWiFi" --psk "pw" --iface wlan0 --iface wlan1

set -eo pipefail

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 1; }; }
need nmcli

SSID=""
PSK=""
OPEN=0
IFACES=("wlan0" "wlan1")

print_usage() {
  cat <<EOF
Usage: sudo $0 --ssid SSID [--psk PSK | --open] [--iface IFACE ...]
  --ssid   SSID name to connect
  --psk    WPA2/WPA3 pre-shared key (omit if using --open)
  --open   Use open network (no password)
  --iface  Interface(s) to configure; repeat or comma-separated (default: wlan0,wlan1)
  -h|--help  Show this help

Examples:
  sudo $0 --ssid "re3D" --psk "SuperSecret"
  sudo $0 --ssid "Guest" --open --iface wlan1
  sudo $0 --ssid "ShopNet" --psk "pw" --iface wlan0 --iface wlan2
EOF
}

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssid) SSID="$2"; shift 2 ;;
    --psk)  PSK="$2"; shift 2 ;;
    --open) OPEN=1; shift ;;
    --iface)
      # support --iface a,b,c or repeated flags
      if [[ "$2" == *","* ]]; then
        IFS=',' read -r -a IFACES <<<"$2"
      else
        IFACES+=("$2")
      fi
      # remove default if user specified any
      IFACES=($(printf "%s\n" "${IFACES[@]}" | awk 'NF' | awk '!seen[$0]++'))
      shift 2
      ;;
    -h|--help) print_usage; exit 0 ;;
    *) echo "Unknown arg: $1"; print_usage; exit 2 ;;
  esac
done

# if user provided at least one --iface, ensure we don’t keep the original defaults duplicated
if [[ "${#IFACES[@]}" -gt 2 ]]; then
  # remove the initial default pair if user added new ones explicitly
  # (already deduped above; nothing else needed)
  :
fi

# validations
if [[ -z "$SSID" ]]; then echo "Error: --ssid is required."; print_usage; exit 2; fi
if [[ $OPEN -eq 1 && -n "$PSK" ]]; then echo "Note: --open provided; ignoring --psk."; PSK=""; fi
if [[ $OPEN -ne 1 && -z "$PSK" ]]; then
  echo "Error: provide --psk for secured networks, or use --open for no password."
  exit 2
fi

dev_exists() { nmcli -t -f DEVICE device | grep -Fxq "$1"; }

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
# de-duplicate IFACES array in case defaults + args collided
mapfile -t IFACES < <(printf "%s\n" "${IFACES[@]}" | awk 'NF' | awk '!seen[$0]++')
for iface in "${IFACES[@]}"; do
  if dev_exists "$iface"; then
    make_and_up "$iface" || rc=1
  else
    echo "Skipping $iface: device not found."
  fi
done
exit "$rc"
