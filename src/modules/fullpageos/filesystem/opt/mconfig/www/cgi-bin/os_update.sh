#!/bin/bash
STATE_FILE="/opt/mconfig/www/update.txt"
REBOOT_CGI="/opt/mconfig/www/cgi-bin/reboot.sh"

# CGI header
echo "Content-Type: text/plain"
echo

# Mark update as running
echo "running" > "$STATE_FILE"

# --- real update logic ---
# Run as root (configure sudoers if invoked as www-data)
LOG=$(/usr/local/bin/re3d-os-update 2>&1)
rc=$?

# Print logs back to the client
echo "$LOG"

if [ "$rc" -eq 0 ]; then
  # Mark that we’re about to reboot
  echo "rebooting" > "$STATE_FILE"
  echo
  echo "Update completed successfully. Rebooting in 5 seconds..."

  # Call existing reboot.sh in the background so CGI can finish cleanly
  # If reboot.sh itself needs sudo, configure that *inside* reboot.sh
  ( sleep 5; "$REBOOT_CGI" >/dev/null 2>&1 ) &
else
  echo "error" > "$STATE_FILE"
  echo
  echo "Update failed with exit code $rc."
fi

exit "$rc"