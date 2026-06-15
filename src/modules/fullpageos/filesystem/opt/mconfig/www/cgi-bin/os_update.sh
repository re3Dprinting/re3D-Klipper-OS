#!/bin/bash
STATE_FILE="/opt/mconfig/www/update.txt"
REBOOT_CGI="/opt/mconfig/www/cgi-bin/reboot.sh"   # adjust if your reboot script lives elsewhere

# CGI header
echo "Content-Type: text/plain"
echo

# Mark update as running
echo "running" > "$STATE_FILE"
echo "Starting OS update..."
echo

# --- real update logic ---
# Read 'reflash' from POST body (1 = reflash after update, 0 = skip; default 1)
POST_BODY=""
if [ "${REQUEST_METHOD:-}" = "POST" ] && [ -n "${CONTENT_LENGTH:-}" ] && [ "${CONTENT_LENGTH:-0}" -gt 0 ] 2>/dev/null; then
  read -r -n "${CONTENT_LENGTH}" POST_BODY 2>/dev/null || true
fi
_REFLASH=$(echo "$POST_BODY" | sed -n 's/.*reflash=\([01]\).*/\1/p' | head -1)
export RE3D_SKIP_REFLASH
[ "${_REFLASH:-1}" = "0" ] && RE3D_SKIP_REFLASH=1 || RE3D_SKIP_REFLASH=0

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
  ( sleep 5; "$REBOOT_CGI" >/dev/null 2>&1 ) &
else
  echo "error" > "$STATE_FILE"
  echo "Update failed with exit code $rc."
fi

exit "$rc"