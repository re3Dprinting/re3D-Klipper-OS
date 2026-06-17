#!/bin/bash
# CGI: schedule an Archimajor board firmware re-flash and reboot.
# Sets the firstboot-splash flag so flash_once.service runs on next boot.

echo "Content-Type: text/plain"
echo ""

touch /etc/firstboot-splash
# Ensure the service file is current and symlink points to the right target.
# This fixes old images where the service may still use the wrong WantedBy.
if [ -f /opt/re3d-os-src/src/modules/fullpageos/filesystem/root_init/etc/systemd/system/flash_once.service ]; then
  install -m 0644 -o root -g root \
    /opt/re3d-os-src/src/modules/fullpageos/filesystem/root_init/etc/systemd/system/flash_once.service \
    /etc/systemd/system/flash_once.service
  rm -f /etc/systemd/system/graphical.target.wants/flash_once.service 2>/dev/null || true
  rm -f /etc/systemd/system/multi-user.target.wants/flash_once.service 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
fi
systemctl enable flash_once.service 2>/dev/null || true

echo "Reflash scheduled. Printer is rebooting now…"

# Reboot in background so the CGI response can reach the browser first
( sleep 3; /sbin/reboot ) &
