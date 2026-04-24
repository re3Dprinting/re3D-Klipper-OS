#!/usr/bin/env bash
# /cgi-bin/wifi_action.sh
# POST: action=disconnect|forget  ssid=<network name>
# Disconnect from or forget (delete saved profile) a WiFi network.

set -eo pipefail

echo "Content-Type: text/plain"
echo

export PATH="/usr/sbin:/sbin:/usr/bin:/bin"

if ! command -v nmcli >/dev/null 2>&1; then
  echo "Error: nmcli not found"
  exit 1
fi

# ── Read POST body ──
read_stdin() {
  local len="${CONTENT_LENGTH:-0}"
  local data=""
  if [[ "$len" -gt 0 ]]; then
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
  local name="$1" src="$2"
  local val
  val=$(printf '%s' "$src" | tr '&' '\n' | sed -n "s/^${name}=//p" | head -n1)
  printf '%s' "$val"
}

BODY="$(read_stdin)"
ACTION="$(urldecode "$(extract_field "action" "$BODY")")"
SSID="$(urldecode "$(extract_field "ssid" "$BODY")")"

if [[ -z "$ACTION" ]]; then
  echo "Error: missing 'action' parameter (disconnect|forget)"
  exit 2
fi

case "$ACTION" in
  disconnect)
    # Disconnect all wifi interfaces
    for iface in wlan0 wlan1; do
      if nmcli -t -f DEVICE device | grep -Fxq "$iface"; then
        nmcli device disconnect "$iface" 2>/dev/null && \
          echo "✅ Disconnected $iface" || \
          echo "⚠️  $iface was not connected"
      fi
    done
    ;;

  forget)
    if [[ -z "$SSID" ]]; then
      echo "Error: missing 'ssid' parameter for forget"
      exit 2
    fi
    found=0
    # Find and delete all connection profiles matching the SSID
    while IFS= read -r cname; do
      [[ -z "$cname" ]] && continue
      con_ssid="$(nmcli -g 802-11-wireless.ssid connection show "$cname" 2>/dev/null || true)"
      if [[ "$con_ssid" == "$SSID" ]]; then
        nmcli connection delete "$cname" >/dev/null 2>&1 && \
          echo "✅ Forgot network '$SSID' (profile: $cname)" || \
          echo "❌ Failed to delete profile '$cname'"
        found=1
      fi
    done < <(nmcli -t -f NAME connection show 2>/dev/null)

    if [[ "$found" -eq 0 ]]; then
      echo "⚠️  No saved profile found for '$SSID'"
    fi
    ;;

  *)
    echo "Error: unknown action '$ACTION' (use disconnect or forget)"
    exit 2
    ;;
esac
