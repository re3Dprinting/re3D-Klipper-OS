#!/bin/bash
set -euo pipefail
LOG=/var/log/flip_to_32bit.log
exec >>"$LOG" 2>&1
CFG="/boot/firmware/config.txt"; [ -f /boot/config.txt ] && CFG="/boot/config.txt"
chg=0
if ! grep -q '^arm_64bit=0' "$CFG"; then
  sed -i '/^arm_64bit=/d' "$CFG"; echo 'arm_64bit=0' >> "$CFG"; chg=1
fi
if (( chg )); then
  touch /var/lib/flip_to_32bit.changed
  systemctl --no-block reboot
  exit 0
fi
touch /var/lib/flip_to_32bit.done
