#!/bin/bash
# /cgi-bin/set_display_mode.sh
# Accepts POST body: mode=mainsail  OR  mode=klipperscreen
# Writes /etc/re3d-display-mode and schedules a reboot.
set -euo pipefail

echo "Content-Type: application/json"
echo ""

# Read POST body
read -r -n "${CONTENT_LENGTH:-0}" POST_DATA 2>/dev/null || POST_DATA=""

# Extract and validate mode value (strict allow-list)
MODE="$(printf '%s' "$POST_DATA" | sed -n 's/.*\bmode=\([^&]*\).*/\1/p' | head -1)"

if [ "$MODE" = "mainsail" ] || [ "$MODE" = "klipperscreen" ]; then
  printf '%s' "$MODE" > /etc/re3d-display-mode
  printf '{"status":"ok","mode":"%s"}\n' "$MODE"
  # Reboot in background so the CGI response returns first
  ( sleep 2 && /bin/systemctl reboot >/dev/null 2>&1 ) &
else
  printf '{"status":"error","message":"Invalid mode. Use mainsail or klipperscreen."}\n'
fi
