#!/bin/bash
# CGI: schedule an Archimajor board firmware re-flash and reboot.
# Sets the firstboot-splash flag so flash_once.service runs on next boot.

echo "Content-Type: text/plain"
echo ""

touch /etc/firstboot-splash
systemctl enable flash_once.service 2>/dev/null || true

echo "Reflash scheduled. Printer is rebooting now…"

# Reboot in background so the CGI response can reach the browser first
( sleep 3; /sbin/reboot ) &
