#!/bin/bash
# /cgi-bin/set_display_mode.sh
# Accepts POST body: mode=mainsail  OR  mode=klipperscreen
# Writes /etc/re3d-display-mode; reboot=defer lets setup finish before rebooting.
set -euo pipefail

reply() {
  printf 'Status: %s\r\nContent-Type: application/json\r\n\r\n%s\n' "$1" "$2"
}

if [ "${REQUEST_METHOD:-}" != "POST" ]; then
  reply "405 Method Not Allowed" '{"status":"error","message":"Expected POST."}'
  exit 1
fi

# Read POST body
POST_DATA=""
read -r -N "${CONTENT_LENGTH:-0}" POST_DATA || true

# Extract and validate mode value (strict allow-list)
MODE="$(printf '%s' "$POST_DATA" | tr '&' '\n' | sed -n 's/^mode=//p' | head -1)"
REBOOT="$(printf '%s' "$POST_DATA" | tr '&' '\n' | sed -n 's/^reboot=//p' | head -1)"
if [ -n "$REBOOT" ] && [ "$REBOOT" != "defer" ]; then
  reply "400 Bad Request" '{"status":"error","message":"Invalid reboot option."}'
  exit 1
fi

if [ "$MODE" = "mainsail" ] || [ "$MODE" = "klipperscreen" ]; then
  if ! printf '%s' "$MODE" > /etc/re3d-display-mode; then
    reply "500 Internal Server Error" '{"status":"error","message":"Unable to save display mode."}'
    exit 1
  fi
  reply "200 OK" "{\"status\":\"ok\",\"mode\":\"$MODE\"}"
  # Reboot in background so the CGI response returns first
  if [ "$REBOOT" != "defer" ]; then
    ( sleep 2 && /bin/systemctl reboot >/dev/null 2>&1 ) &
  fi
else
  reply "400 Bad Request" '{"status":"error","message":"Invalid mode. Use mainsail or klipperscreen."}'
  exit 1
fi
