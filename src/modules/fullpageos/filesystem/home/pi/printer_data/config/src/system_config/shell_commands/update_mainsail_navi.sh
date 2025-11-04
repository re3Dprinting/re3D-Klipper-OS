#!/usr/bin/env bash
set -euo pipefail

# --- Settings ---
TITLE="Configurator"
PORT="${PORT:-8081}"
ICON_PATH='M3,17V19H9V17H3M3,5V7H13V5H3M13,21V19H21V17H13V15H11V21H13M7,9V11H3V13H7V15H9V9H7M21,13V11H11V13H21M15,9H17V7H21V5H17V3H15V9Z'
POSITION=90
TARGET="_self"
USER_HOME="/home/pi"                       # change if your user isn't 'pi'

# Detect where Mainsail reads config
if [ -d "$USER_HOME/printer_data/config" ]; then
  THEME_DIR="$USER_HOME/printer_data/config/.theme"
elif [ -d "$USER_HOME/klipper_config" ]; then
  THEME_DIR="$USER_HOME/klipper_config/.theme"
else
  # default to printer_data path
  THEME_DIR="$USER_HOME/printer_data/config/.theme"
fi

mkdir -p "$THEME_DIR"

# Pick best private IPv4: eth0 > wlan0 > anything private; fallback 127.0.0.1
pick_ip() {
  # helper: first private IPv4 on an interface (if any)
  int_ip() {
    local IF="$1"
    ip -4 -o addr show dev "$IF" scope global 2>/dev/null \
      | awk '{print $4}' | cut -d/ -f1 \
      | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)' | head -n1 || true
  }

  # priority: eth0, wlan0, then any interface
  for IF in eth0 wlan0; do
    IP=$(int_ip "$IF")
    [ -n "${IP:-}" ] && { echo "$IP"; return; }
  done

  # any private IPv4 on any interface
  IP=$(ip -4 -o addr show scope global \
        | awk '{print $4}' | cut -d/ -f1 \
        | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)' \
        | head -n1 || true)
  [ -n "${IP:-}" ] && { echo "$IP"; return; }

  echo "127.0.0.1"
}

IP="$(pick_ip)"
URL="http://${IP}:${PORT}/"

TMP="$(mktemp)"
cat >"$TMP" <<JSON
[
  {
    "title": "${TITLE}",
    "href": "${URL}",
    "position": ${POSITION},
    "target": "${TARGET}",
    "icon": "${ICON_PATH}"
  }
]
JSON

install -o pi -g pi -m 0644 "$TMP" "$THEME_DIR/navi.json"
rm -f "$TMP"

# Optional: write a tiny marker for debugging
echo "navi.json -> ${URL} ($(date -Is))" | tee "$THEME_DIR/.navi.last"