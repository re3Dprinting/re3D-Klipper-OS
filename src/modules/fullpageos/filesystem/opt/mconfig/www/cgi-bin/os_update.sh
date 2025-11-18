#!/bin/bash
STATE_FILE="/opt/mconfig/www/update.txt"

# CGI header
echo "Content-Type: text/plain"
echo

# Mark update as running
echo "running" > "$STATE_FILE"

# --- real update logic ---
# Run as root (configure sudoers if invoked as www-data)
# Default branch (devel); change or add logic for main/stable if you want
LOG=$(/usr/local/bin/re3d-os-update 2>&1)
rc=$?

# Print logs back to the client
echo "$LOG"

# Update state file based on result
if [ "$rc" -eq 0 ]; then
  echo "reboot_required" > "$STATE_FILE"
else
  echo "error" > "$STATE_FILE"
fi
sleep 5
exit "$rc" && reboot