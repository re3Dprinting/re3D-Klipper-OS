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
  mountpoint -q "${MNT_ROOT}/dev/pts" && sudo umount -R "${MNT_ROOT}/dev/pts"
  mountpoint -q "${MNT_ROOT}/dev"     && sudo umount -R "${MNT_ROOT}/dev"
  mountpoint -q "${MNT_ROOT}/proc"    && sudo umount -R "${MNT_ROOT}/proc"
  mountpoint -q "${MNT_ROOT}/sys"     && sudo umount -R "${MNT_ROOT}/sys"
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
  # root part (no delete)
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

###############################################################################
# 6) OWNERSHIP NORMALIZATION
# keep /home/pi as pi:pi
###############################################################################
if [ -d "${MNT_ROOT}/home/pi" ]; then
  sudo chown -R 1000:1000 "${MNT_ROOT}/home/pi"
fi

# do not chown -R /opt/custompios because some files must stay root

# fix mixed ownership in /opt/custompios/scripts to what you found works
SCRIPTS_DIR="${MNT_ROOT}/opt/custompios/scripts"
if [ -d "$SCRIPTS_DIR" ]; then
  # files that should be pi:pi
  for f in enable_gpu start_gui update_lightdm_conf; do
    if [ -e "$SCRIPTS_DIR/$f" ]; then
      sudo chown 1000:1000 "$SCRIPTS_DIR/$f"
      sudo chmod 755 "$SCRIPTS_DIR/$f"
    fi
  done

  # files that should be root:root
  for f in fullscreen get_url refresh reload_fullpageos_txt rotate.sh \
           run_onepageos safe_refresh setX11vncPass start_chromium_browser; do
    if [ -e "$SCRIPTS_DIR/$f" ]; then
      sudo chown root:root "$SCRIPTS_DIR/$f"
      # rotate.sh was rw-r--r-- in your listing so keep that
      if [ "$f" = "rotate.sh" ] || [ "$f" = "safe_refresh" ]; then
        sudo chmod 644 "$SCRIPTS_DIR/$f"
      else
        sudo chmod 755 "$SCRIPTS_DIR/$f"
      fi
    fi
  done
fi

# sometimes printer_data lives here
if [ -d "${MNT_ROOT}/printer_data" ]; then
  sudo chown -R 1000:1000 "${MNT_ROOT}/printer_data"
fi

# safety sweep for odd uids that should be pi
for path in \
  "${MNT_ROOT}/home" \
  "${MNT_ROOT}/opt" \
  "${MNT_ROOT}/printer_data"
do
  if [ -d "$path" ]; then
    sudo find "$path" -xdev -uid 1001 -exec chown 1000:1000 {} +
  fi
done

# 7) make key scripts executable (the one that failed in your log)
if [ -f "${MNT_ROOT}/home/pi/printer_data/config/src/get_serial.sh" ]; then
  sudo chmod +x "${MNT_ROOT}/home/pi/printer_data/config/src/get_serial.sh"
fi

# also make every .sh in that dir executable
if [ -d "${MNT_ROOT}/home/pi/printer_data/config/src" ]; then
  sudo find "${MNT_ROOT}/home/pi/printer_data/config/src" -type f -name '*.sh' -exec chmod +x {} +
fi

###############################################################################

# 8) flush & unmount
sync
unmount_all

# 9) fsck after unmount
if command -v e2fsck >/dev/null 2>&1; then
  echo "Running offline fsck on ${ROOT_PART}"
  sudo e2fsck -f -p "${ROOT_PART}" || true
else
  echo "Warning: e2fsck not found; skipping offline fsck"
fi

# 10) detach & write out
sudo losetup -d "${LOOP}"
LOOP=""
cp -f "${WORK_IMG}" "${IMG_OUT}"
echo "Wrote overlayed image to: ${IMG_OUT}"