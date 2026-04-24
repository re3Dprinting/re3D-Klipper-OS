#!/bin/bash
# /cgi-bin/get_display_mode.sh
# Returns the current display mode: "mainsail" or "klipperscreen"
echo "Content-Type: text/plain"
echo ""
cat /etc/re3d-display-mode 2>/dev/null || echo "mainsail"
