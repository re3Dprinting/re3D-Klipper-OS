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

# --- NEW: make sure klipper tree exists and normalize timestamps ---
normalize_klipper_tree(){
  echo "$(ts) normalize_klipper_tree: ensuring ~/klipper exists and mtimes sane"
  jstatus "running" 10 "Preparing Klipper sources"

  # Everything below runs as 'pi' so ownership/permissions are correct.
  sudo -u pi bash -lc '
    set -e
    KLIP=~/klipper
    if [ ! -d "$KLIP" ]; then
      echo "ERROR: ~/klipper missing"; exit 3
    fi

    cd "$KLIP"

    # If git repo, make sure it is usable even if owner changed during image build
    if command -v git >/dev/null 2>&1 && [ -d .git ]; then
      git config --global --add safe.directory "$KLIP" || true
    fi

    # Touch every file to "now - 2 minutes" to avoid future-mtime warnings when offline.
    # Use -h to avoid dereferencing symlinks; skip .git objects for speed.
    find . -path "./.git" -prune -o -type f -print0 | xargs -0 touch -m -a -d "now - 2 minutes" || true
  '
}

# --- NEW: write .config on first boot (not in chroot) and fill defaults ---
ensure_klipper_config(){
  echo "$(ts) ensure_klipper_config: writing .config"
  jstatus "running" 20 "Staging Klipper .config"

  sudo -u pi bash -lc '
    set -e
    KLIP=~/klipper
    cd "$KLIP"
    cat > .config << "EOF"
# Klipper firmware config: Atmel SAM3/SAM4 -> SAM3X8E, USB CDC
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_ATSAM=y
CONFIG_BOARD_DIRECTORY="atsam"
CONFIG_MCU="sam3x8e"
CONFIG_CLOCK_REF_12M=y
CONFIG_USBSERIAL=y
EOF
    chmod 0644 .config

    # Fill in any missing options without TUI
    make olddefconfig
  '
}

echo "$(ts) flash_once: starting"
jstatus "starting" 0 "starting"
list_devices_json

# Prepare sources and config now (first boot), not in chroot
normalize_klipper_tree
ensure_klipper_config

# --- Wait for ERASED device before flashing ---
echo "$(ts) waiting for erased device: $ERASED_PATH"
jstatus "waiting_device" 5 "No connection detected. Check printer power and USB cable."
end=$((SECONDS + PRE_FLASH_MAX_WAIT))
while :; do
  list_devices_json
  if [[ -e "$ERASED_PATH" ]]; then
    echo "$(ts) found erased device"
    jstatus "running" 30 "Detected erased board" "$ERASED_PATH"
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

# --- Flash sequence (as pi for sane perms) ---
if ! sudo -u pi test -d /home/pi/klipper/ ; then
  echo "$(ts) ERROR: /home/pi/klipper missing"
  jstatus "error" 5 "/home/pi/klipper not found"
  exit 0
fi

echo "$(ts) make clean"
jstatus "running" 40 "Cleaning build"
sudo -u pi bash -lc 'cd ~/klipper && make clean || true'

echo "$(ts) stopping klipper"
jstatus "running" 50 "Stopping Klipper"
systemctl stop klipper || true

# Ensure bossac is available — pre-installed in new images, but install as a fallback
# for older images that were built before bossa-cli was added to the chroot.
if ! command -v bossac >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y bossa-cli 2>/dev/null || true
fi

# Pre-build klipper.bin once so both make-flash and bossac can use it
if [[ ! -f /home/pi/klipper/out/klipper.bin ]]; then
  echo "$(ts) pre-building klipper.bin"
  jstatus "running" 55 "Building firmware binary"
  sudo -u pi bash -lc 'cd ~/klipper && make clean && make' || true
fi

# --- Helpers ---

resolve_acm_port(){
  local rp
  rp="$(realpath -e "$ERASED_PATH" 2>/dev/null || true)"
  if [[ "$rp" == /dev/ttyACM* ]]; then echo "$rp"; return; fi
  for p in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2; do
    [[ -e "$p" ]] && echo "$p" && return
  done
}

# --- Flash with retry loop ---
FLASH_MAX_ATTEMPTS=5
FLASH_RETRY_DELAY=10
FLASH_CMD_OK=0

for attempt in $(seq 1 "$FLASH_MAX_ATTEMPTS"); do
  echo "$(ts) === Flash attempt ${attempt}/${FLASH_MAX_ATTEMPTS} ==="
  jstatus "running" 80 "Flashing firmware (attempt ${attempt}/${FLASH_MAX_ATTEMPTS})"

  udevadm settle --timeout=5 || true
  sleep 1
  list_devices_json

  FLASH_CMD_OK=0

  # --- Method 1: make flash ---
  if [[ -e "$ERASED_PATH" ]]; then
    echo "$(ts) trying make flash on $ERASED_PATH"
    sudo -u pi bash -lc "cd ~/klipper && make flash FLASH_DEVICE='$ERASED_PATH'" 2>&1 | tee -a "$LOG"
    if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
      echo "$(ts) make flash reported success on attempt ${attempt}"
      FLASH_CMD_OK=1
    else
      echo "$(ts) make flash FAILED on attempt ${attempt}"
    fi
  else
    echo "$(ts) erased device $ERASED_PATH not present, skipping make flash"
  fi

  # --- Method 2: bossac fallback (only if make flash failed) ---
  if (( ! FLASH_CMD_OK )); then
    if command -v bossac >/dev/null 2>&1 && [[ -f /home/pi/klipper/out/klipper.bin ]]; then
      ACM_PORT="$(resolve_acm_port)"
      if [[ -n "$ACM_PORT" ]]; then
        echo "$(ts) trying bossac on $ACM_PORT (attempt ${attempt})"
        jstatus "running" 82 "Trying bossac on ${ACM_PORT} (attempt ${attempt}/${FLASH_MAX_ATTEMPTS})"
        sudo -u pi bossac -U -p "$ACM_PORT" -a -e -w /home/pi/klipper/out/klipper.bin -v -b 2>&1 | tee -a "$LOG"
        if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
          echo "$(ts) bossac reported success on $ACM_PORT (attempt ${attempt})"
          FLASH_CMD_OK=1
        else
          echo "$(ts) bossac FAILED on $ACM_PORT (attempt ${attempt})"
        fi
      fi
    fi
  fi

  if (( FLASH_CMD_OK )); then
    echo "$(ts) flash succeeded on attempt ${attempt}"
    break
  fi

  echo "$(ts) both flash methods failed on attempt ${attempt}"
  if (( attempt < FLASH_MAX_ATTEMPTS )); then
    jstatus "running" 80 "Flash command failed — retrying in ${FLASH_RETRY_DELAY}s"
    sleep "$FLASH_RETRY_DELAY"
  fi
done

# --- Ensure build deps + venv exist (safe to re-run) ---
echo "$(ts) ensuring build deps + klippy-env"
jstatus "running" 96 "Preparing build environment"
DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential python3-dev libffi-dev || true

sudo -u pi -H bash -lc '
  set -e
  if [ ! -x "$HOME/klippy-env/bin/python" ]; then
    python3 -m venv "$HOME/klippy-env"
    "$HOME/klippy-env/bin/pip" install --upgrade pip wheel
    "$HOME/klippy-env/bin/pip" install -r "$HOME/klipper/scripts/klippy-requirements.txt"
  fi
'
#systemctl enable splash_video.service || true

echo "$(ts) starting klipper"
jstatus "running" 98 "Starting Klipper"
systemctl start klipper || true

# --- FINAL: branch on flash result ---
if (( FLASH_CMD_OK )); then
  echo "$(ts) Flash command succeeded"
  jstatus "power_cycle" 100 "Flash complete! Please power-cycle the machine to complete setup."

  # Clear first-boot flags so next boot is normal
  rm -f /etc/firstboot-splash /tmp/firstboot-ui-started
else
  echo "$(ts) ERROR: Flash FAILED after ${FLASH_MAX_ATTEMPTS} attempts"
  jstatus "error" 85 "Firmware flash failed after ${FLASH_MAX_ATTEMPTS} attempts. Please power-cycle and try again."

  # Do NOT clear firstboot-splash — the flash will be re-attempted on next boot
  rm -f /tmp/firstboot-ui-started
fi

# Park here forever so the splash stays visible
while :; do sleep 3600; done

# (never reached)
exit 0
