#!/bin/bash
STATE_FILE="/opt/mconfig/www/update.txt"
REBOOT_CGI="/cgi-bin/reboot.sh"   # adjust if your reboot script lives elsewhere

# CGI header
echo "Content-Type: text/plain"
echo

# Mark update as running
echo "running" > "$STATE_FILE"
echo "Starting OS update..."
echo

# --- real update logic ---
# Run as root (configure sudoers if invoked as www-data)
LOG=$(/usr/local/bin/re3d-os-update 2>&1)
rc=$?

# Print logs back to the client
echo "$LOG"
echo

if [ "$rc" -eq 0 ]; then
  # Mark that we’re about to reboot
  echo "rebooting" > "$STATE_FILE"
  echo "Update completed successfully. Rebooting in 10 seconds..."

  # Reboot in the background after a short delay so the UI can update
  ( sleep 10; "$REBOOT_CGI" >/dev/null 2>&1 ) &
else
  echo "error" > "$STATE_FILE"
  echo "Update failed with exit code $rc."
fi

exit "$rc"