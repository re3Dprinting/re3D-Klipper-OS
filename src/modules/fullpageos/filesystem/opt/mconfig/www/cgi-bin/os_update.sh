#!/bin/bash
STATE_FILE="/opt/mconfig/www/update.txt"

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
  # Mark that we’re about to reboot (you can keep "reboot_required" if your JS expects that)
  echo "rebooting" > "$STATE_FILE"
  echo
  echo "Update completed successfully. Rebooting in 5 seconds..."

  # Reboot in the background so CGI can finish and the browser gets this text
  # If you need sudo, use: ( sleep 5; sudo /sbin/reboot ) &
  ( sleep 5; /sbin/reboot ) &
else
  echo "error" > "$STATE_FILE"
  echo
  echo "Update failed with exit code $rc."
fi

exit "$rc"