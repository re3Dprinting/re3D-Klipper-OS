#!/usr/bin/env bash
set -euo pipefail

# --- Settings ---
TITLE="Configurator"
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

URL="/configurator/"

TMP="$(mktemp "$THEME_DIR/.navi.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
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

if [ -f "$THEME_DIR/navi.json" ] && cmp -s "$TMP" "$THEME_DIR/navi.json"; then
  exit 0
fi

chown pi:pi "$TMP"
chmod 0644 "$TMP"
mv -f "$TMP" "$THEME_DIR/navi.json"

# Optional: write a tiny marker for debugging
echo "navi.json -> ${URL} ($(date -Is))" | tee "$THEME_DIR/.navi.last"