#!/bin/bash
# Minimal BusyBox httpd CGI for reboot

echo "Content-Type: text/plain"
echo ""

# Kick the reboot in the background so CGI can return immediately
if command -v systemctl >/dev/null 2>&1; then
  sudo /bin/systemctl reboot >/dev/null 2>&1 &
elif command -v reboot >/dev/null 2>&1; then
  sudo /sbin/reboot >/dev/null 2>&1 &
else
  echo "No reboot command found."
  exit 1
fi

echo "Rebooting…"