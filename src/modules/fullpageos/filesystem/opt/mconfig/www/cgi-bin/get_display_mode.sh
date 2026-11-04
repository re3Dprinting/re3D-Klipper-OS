#!/bin/bash
# /cgi-bin/get_display_mode.sh
# Returns the current display mode: "mainsail" or "klipperscreen"
echo "Content-Type: text/plain"
echo ""
if [ -f /etc/re3d-security/lockdown ]; then
  echo "mainsail"
  exit 0
fi
cat /etc/re3d-display-mode 2>/dev/null || echo "mainsail"
