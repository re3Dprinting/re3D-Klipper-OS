#!/usr/bin/env bash
set -euo pipefail

IMG_IN="$1"
IMG_OUT="$2"

WORK_DIR="$(mktemp -d)"
WORK_IMG="${WORK_DIR}/work.img"
MNT_ROOT="${WORK_DIR}/mnt"
MNT_BOOT="${WORK_DIR}/mnt_boot"
LOOP=""

unmount_all() {
  set +e
  mountpoint -q "${MNT_BOOT}" && sudo umount -R "${MNT_BOOT}"
  mountpoint -q "${MNT_ROOT}" && sudo umount -R "${MNT_ROOT}"
  set -e
}

cleanup() {
  set +e
  unmount_all || true
  [ -n "${LOOP}" ] && sudo losetup -d "${LOOP}" || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

mkdir -p "${MNT_ROOT}" "${MNT_BOOT}"

# 1) work on a copy
cp -f --reflink=auto "${IMG_IN}" "${WORK_IMG}"

# 2) attach & mount
LOOP="$(sudo losetup -fP --show "${WORK_IMG}")"
ROOT_PART="${LOOP}p2"
BOOT_PART="${LOOP}p1"

if [ ! -b "${ROOT_PART}" ]; then
  echo "ERROR: root partition ${ROOT_PART} not found"
  ls -l "${LOOP}"*
  exit 1
fi

sudo mount "${ROOT_PART}" "${MNT_ROOT}"
if [ -b "${BOOT_PART}" ]; then
  sudo mount "${BOOT_PART}" "${MNT_BOOT}"
fi

# 3) find overlay dir
OVERLAY_DIR=""
if   [ -d filesystem ]; then
  OVERLAY_DIR="filesystem"
elif [ -d src/modules/fullpageos/filesystem ]; then
  OVERLAY_DIR="src/modules/fullpageos/filesystem"
elif [ -d modules/fullpageos/filesystem ]; then
  OVERLAY_DIR="modules/fullpageos/filesystem"
fi

# 4) apply overlay
if [ -n "${OVERLAY_DIR}" ]; then
  echo "Applying overlay from: ${OVERLAY_DIR}"
  # root part
  sudo rsync -a --exclude 'boot/' "${OVERLAY_DIR}/" "${MNT_ROOT}/"

  # boot part
  if [ -d "${OVERLAY_DIR}/boot" ] && mountpoint -q "${MNT_BOOT}"; then
    echo "Applying boot overlay from: ${OVERLAY_DIR}/boot -> p1"
    sudo rsync -rltD --no-owner --no-group --no-perms --modify-window=1 \
      "${OVERLAY_DIR}/boot/" "${MNT_BOOT}/"
  fi
else
  echo "No overlay directory found"
fi

# 5) make sure /host_cache exists
if [ ! -d "${MNT_ROOT}/host_cache" ]; then
  sudo mkdir -p "${MNT_ROOT}/host_cache"
  sudo chmod 0755 "${MNT_ROOT}/host_cache"
fi

# 6) **normalize ownership** to match what the big script does
# everything under /home/pi should belong to pi:pi (1000:1000) on Raspberry Pi OS
if [ -d "${MNT_ROOT}/home/pi" ]; then
  sudo chown -R 1000:1000 "${MNT_ROOT}/home/pi"
fi

# 7) **fix execute bit** on the script that reload.py calls
if [ -f "${MNT_ROOT}/home/pi/printer_data/config/src/get_serial.sh" ]; then
  sudo chmod +x "${MNT_ROOT}/home/pi/printer_data/config/src/get_serial.sh"
fi

# (optional) sometimes people also have helper scripts here:
# sudo chmod +x "${MNT_ROOT}/home/pi/printer_data/config/src/reload.py" || true

# 8) flush, unmount, fsck
sync
unmount_all

if command -v e2fsck >/dev/null 2>&1; then
  echo "Running offline fsck on ${ROOT_PART}"
  sudo e2fsck -f -p "${ROOT_PART}" || true
else
  echo "Warning: e2fsck not found; skipping offline fsck"
fi

# 9) detach and write out
sudo losetup -d "${LOOP}"
LOOP=""
cp -f "${WORK_IMG}" "${IMG_OUT}"
echo "Wrote overlayed image to: ${IMG_OUT}"