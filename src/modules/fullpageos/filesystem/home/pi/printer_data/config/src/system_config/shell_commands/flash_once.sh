#!/usr/bin/env bash
# Flash → prompt user to power-cycle indefinitely (no reboot, no post-checks).

set -u
LOG=/var/log/flash_once.log
STATUS=/tmp/flash_status.json
DEVLIST=/tmp/flash_devices.json

ERASED_PATH="/dev/serial/by-id/usb-03eb_6124-if00"  # erased Atmel ID

PRE_FLASH_MAX_WAIT=3600  # 60 min to find erased device
POLL=2

exec >>"$LOG" 2>&1

ts(){ date +"%F %T"; }
jstatus(){ # jstatus <state> <progress> <message> [device]
  local state="${1:-running}" p="${2:-0}" msg="${3:-""}" dev="${4:-""}"
  printf '{"state":"%s","progress":%s,"message":"%s","device":"%s"}\n' \
         "$state" "$p" "$msg" "$dev" > "$STATUS"
}

list_devices_json(){
  local out="["; local first=1
  shopt -s nullglob
  local DIR=/dev/serial/by-id
  if [[ -d "$DIR" ]]; then
    for p in "$DIR"/*; do
      local rp; rp="$(realpath -e "$p" 2>/dev/null || echo "")"
      local item; item=$(printf '{"path":"%s","realpath":"%s"}' "$p" "${rp:-""}")
      if (( first )); then out="$out$item"; first=0; else out="$out,$item"; fi
    done
  fi
  out="$out]"
  printf '%s\n' "$out" > "$DEVLIST"
}

echo "$(ts) flash_once: starting"
jstatus "starting" 0 "starting"
list_devices_json

# --- Wait for ERASED device before flashing ---
echo "$(ts) waiting for erased device: $ERASED_PATH"
jstatus "waiting_device" 5 "No connection detected. Check printer power and USB cable."
end=$((SECONDS + PRE_FLASH_MAX_WAIT))
while :; do
  list_devices_json
  if [[ -e "$ERASED_PATH" ]]; then
    echo "$(ts) found erased device"
    jstatus "running" 20 "Detected erased board" "$ERASED_PATH"
    break
  fi
  if [[ ! -d /dev/serial/by-id ]] || [[ -z $(/bin/ls -1 /dev/serial/by-id 2>/dev/null) ]]; then
    jstatus "waiting_device" 5 "No connection detected. Check printer power and USB cable."
  else
    jstatus "waiting_device" 5 "Board detected but not ready. Erase it so it appears as the Atmel device."
  fi
  (( SECONDS >= end )) && { echo "$(ts) timeout pre-flash"; jstatus "error" 5 "Timed out waiting for erased board."; exit 0; }
  sleep "$POLL"
done

udevadm settle || true
sleep 0.5

# --- Flash sequence ---
if ! cd /home/pi/klipper/ 2>/dev/null; then
  echo "$(ts) ERROR: /home/pi/klipper missing"
  jstatus "error" 5 "/home/pi/klipper not found"
  exit 0
fi

echo "$(ts) make clean"
jstatus "running" 30 "Cleaning build"
make clean || true

echo "$(ts) stopping klipper"
jstatus "running" 40 "Stopping Klipper"
systemctl stop klipper || true

echo "$(ts) flashing"
jstatus "running" 70 "Flashing firmware"
find . -type f -exec touch -m {} +   # normalize mtimes to current (even if wrong)
make flash FLASH_DEVICE="$ERASED_PATH" || true

echo "$(ts) starting klipper"
jstatus "running" 85 "Starting Klipper"
systemctl start klipper || true

# --- FINAL: Prompt user to power-cycle. Remove flags. Sit here forever. ---
echo "$(ts) prompting for power-cycle (indefinite)"
jstatus "power_cycle" 100 "Flashing complete. Please switch the machine OFF, then ON to power-cycle both the Pi and mainboard."

# Clear first-boot flags so next boot is normal (when user actually power-cycles)
rm -f /etc/firstboot-splash /tmp/firstboot-ui-started

# Park here forever so the splash stays visible
while :; do sleep 3600; done

# (never reached)
exit 0