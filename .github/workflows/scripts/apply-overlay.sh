#!/usr/bin/env bash
set -euo pipefail

IMG_IN="$1"
IMG_OUT="$2"

WORK_DIR="$(mktemp -d)"
WORK_IMG="${WORK_DIR}/work.img"
MNT_ROOT="${WORK_DIR}/mnt"
MNT_BOOT="${WORK_DIR}/mnt_boot"
LOOP=""

# ---------- helpers ----------
unmount_all() {
  set +e
  # Unbinds first (in safe order)
  mountpoint -q "${MNT_ROOT}/dev/pts" && sudo umount -R "${MNT_ROOT}/dev/pts"
  mountpoint -q "${MNT_ROOT}/dev"     && sudo umount -R "${MNT_ROOT}/dev"
  mountpoint -q "${MNT_ROOT}/proc"    && sudo umount -R "${MNT_ROOT}/proc"
  mountpoint -q "${MNT_ROOT}/sys"     && sudo umount -R "${MNT_ROOT}/sys"
  # Then filesystems
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

# 1) Work on a copy so input is never mutated
cp -f --reflink=auto "${IMG_IN}" "${WORK_IMG}"

# 2) Attach loop & mount
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

# 3) Find overlay dir
OVERLAY_DIR=""
if   [ -d filesystem ]; then
  OVERLAY_DIR="filesystem"
elif [ -d src/modules/fullpageos/filesystem ]; then
  OVERLAY_DIR="src/modules/fullpageos/filesystem"
elif [ -d modules/fullpageos/filesystem ]; then
  OVERLAY_DIR="modules/fullpageos/filesystem"
fi

# 4) Apply overlay (root + boot)
if [ -n "${OVERLAY_DIR}" ]; then
  echo "Applying overlay from: ${OVERLAY_DIR}"
  # Root overlay: add/override only (no --delete)
  sudo rsync -a --exclude 'boot/' "${OVERLAY_DIR}/" "${MNT_ROOT}/"

  # Boot overlay (FAT-safe flags)
  if [ -d "${OVERLAY_DIR}/boot" ] && mountpoint -q "${MNT_BOOT}"; then
    echo "Applying boot overlay from: ${OVERLAY_DIR}/boot -> p1"
    sudo rsync -rltD --no-owner --no-group --no-perms --modify-window=1 \
      "${OVERLAY_DIR}/boot/" "${MNT_BOOT}/"
  fi
else
  echo "No overlay directory found (tried filesystem/, src/modules/fullpageos/filesystem/, modules/fullpageos/filesystem/)"
fi

# 5) Tolerance for CustomPiOS unpack
if [ ! -d "${MNT_ROOT}/host_cache" ]; then
  sudo mkdir -p "${MNT_ROOT}/host_cache"
  sudo chmod 0755 "${MNT_ROOT}/host_cache"
fi

# 6) Optional ownership nudge for known files
if [ -f "${MNT_ROOT}/home/pi/wait.html" ]; then
  sudo chown 1000:1000 "${MNT_ROOT}/home/pi/wait.html" 2>/dev/null || true
fi

# 7) Optional fast updater inside chroot
sudo mount --bind /dev  "${MNT_ROOT}/dev"
sudo mount --bind /proc "${MNT_ROOT}/proc"
sudo mount --bind /sys  "${MNT_ROOT}/sys"
sudo mount --bind /dev/pts "${MNT_ROOT}/dev/pts" || true

if sudo chroot "${MNT_ROOT}" /usr/bin/env bash -lc 'test -x /opt/custompios/scripts/update_apps_fast.sh'; then
  echo "Running in-chroot fast updater"
  sudo chroot "${MNT_ROOT}" /usr/bin/env bash -lc '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    /opt/custompios/scripts/update_apps_fast.sh || true
  '
else
  echo "No fast updater present; skipping."
fi

# 8) Flush, then unmount EVERYTHING before fsck
sync
unmount_all

# 9) Offline fsck on the unmounted root partition
if command -v e2fsck >/dev/null 2>&1; then
  echo "Running offline fsck on ${ROOT_PART}"
  # -p (preen) fixes safely; if you truly want full auto, switch to -y
  sudo e2fsck -f -p "${ROOT_PART}"
else
  echo "Warning: e2fsck not found; skipping offline fsck"
fi

# 10) Detach loop and emit the image
sudo losetup -d "${LOOP}"
LOOP=""
cp -f "${WORK_IMG}" "${IMG_OUT}"
echo "Wrote overlayed image to: ${IMG_OUT}"