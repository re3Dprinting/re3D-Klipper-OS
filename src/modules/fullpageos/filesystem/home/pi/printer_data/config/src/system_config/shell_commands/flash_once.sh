#!/usr/bin/env bash
LOG=/var/log/flash_once.log
exec >>"$LOG" 2>&1

echo "[$(date)] flash_once: starting"
cd /home/pi/klipper/ || true
make clean || true
systemctl stop klipper || true
make flash FLASH_DEVICE=/dev/serial/by-id/usb-03eb_6124-if00 || true
systemctl start klipper || true
echo "[$(date)] flash_once: finished"
exit 0
