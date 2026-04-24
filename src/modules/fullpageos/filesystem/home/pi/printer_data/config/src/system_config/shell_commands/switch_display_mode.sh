#!/bin/bash
# switch_display_mode.sh
# Called by Klipper gcode_shell_command to switch the display mode and reboot.
# Usage: switch_display_mode.sh <mainsail|klipperscreen>
set -euo pipefail

MODE="${1:-}"

if [ "$MODE" = "mainsail" ] || [ "$MODE" = "klipperscreen" ]; then
  printf '%s' "$MODE" > /etc/re3d-display-mode
  echo "Display mode set to: $MODE — rebooting..."
  sleep 2
  /sbin/reboot
else
  echo "ERROR: Invalid mode '${MODE}'. Use 'mainsail' or 'klipperscreen'."
  exit 1
fi
