#!/usr/bin/env bash
# One-time flasher with device gating + live status for the splash page.

LOG=/var/log/flash_once.log
STATUS=/tmp/flash_status.json
DEVLIST=/tmp/flash_devices.json
EXPECTED_PATH="/dev/serial/by-id/usb-03eb_6124-if00"   # erased Atmel (03eb:6124)
MAX_WAIT_SEC=3600                                      # wait up to 60 min
SLEEP_SEC=2

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

# --- Wait for expected device ---
echo "$(ts) waiting for expected device: $EXPECTED_PATH"
jstatus "waiting_device" 5 "Waiting for erased board (03eb:6124). If needed, erase manually; this page will update."

end=$((SECONDS+MAX_WAIT_SEC))
while :; do
  list_devices_json

  if [[ -e "$EXPECTED_PATH" ]]; then
    echo "$(ts) found expected device: $EXPECTED_PATH"
    jstatus "running" 20 "Detected erased board" "$EXPECTED_PATH"
    break
  fi

  if [[ ! -d /dev/serial/by-id ]] || [[ -z $(/bin/ls -1 /dev/serial/by-id 2>/dev/null) ]]; then
    jstatus "waiting_device" 5 "No USB serial devices detected. Check cable/power and try another USB port."
  else
    jstatus "waiting_device" 5 "Different serial device(s) detected. Erase the board so it appears as 03eb:6124."
  fi

  (( SECONDS >= end )) && {
    echo "$(ts) timeout waiting for device"
    jstatus "error" 5 "Timed out waiting for erased board (usb-03eb_6124-if00)."
    exit 0
  }

  sleep "$SLEEP_SEC"
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
make flash FLASH_DEVICE="$EXPECTED_PATH" || true

echo "$(ts) starting klipper"
jstatus "running" 85 "Starting Klipper"
systemctl start klipper || true

echo "$(ts) done"
jstatus "done" 100 "Complete"
exit 0
