#!/usr/bin/env bash
set -euo pipefail

IMG_IN="$1"
IMG_OUT="$2"

WORK=$(mktemp -d)
LOOP=""
cleanup() {
  #!/usr/bin/env bash
  set -euo pipefail

  IMG_IN="$1"
  IMG_OUT="$2"

  WORK_DIR="$(mktemp -d)"
  WORK_IMG="${WORK_DIR}/work.img"
  MNT_ROOT="${WORK_DIR}/mnt"
  MNT_BOOT="${WORK_DIR}/mnt_boot"
  LOOP=""

  cleanup() {
    set +e
    # Unmount in reverse order
    mountpoint -q "${MNT_ROOT}/sys"  && sudo umount -R "${MNT_ROOT}/sys"  || true
    mountpoint -q "${MNT_ROOT}/proc" && sudo umount -R "${MNT_ROOT}/proc" || true
    mountpoint -q "${MNT_ROOT}/dev/pts" && sudo umount -R "${MNT_ROOT}/dev/pts" || true
    mountpoint -q "${MNT_ROOT}/dev" && sudo umount -R "${MNT_ROOT}/dev" || true
    mountpoint -q "${MNT_BOOT}" && sudo umount -R "${MNT_BOOT}" || true
    mountpoint -q "${MNT_ROOT}" && sudo umount -R "${MNT_ROOT}" || true
    [ -n "${LOOP}" ] && sudo losetup -d "${LOOP}" || true
    rm -rf "${WORK_DIR}"
  }
  trap cleanup EXIT

  mkdir -p "${MNT_ROOT}" "${MNT_BOOT}"

  # 1) Work on a copy so the input image is never mutated
  cp -f --reflink=auto "${IMG_IN}" "${WORK_IMG}"

  # 2) Attach loop and mount partitions
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

  # 3) Discover overlay root(s)
  OVERLAY_DIR=""
  if [ -d filesystem ]; then
    OVERLAY_DIR=filesystem
  elif [ -d src/modules/fullpageos/filesystem ]; then
    OVERLAY_DIR=src/modules/fullpageos/filesystem
  elif [ -d modules/fullpageos/filesystem ]; then
    OVERLAY_DIR=modules/fullpageos/filesystem
  fi

  if [ -n "${OVERLAY_DIR}" ]; then
    echo "Applying overlay from: ${OVERLAY_DIR}"

    # Rootfs (everything except 'boot/')
    sudo rsync -a --delete --exclude 'boot/' "${OVERLAY_DIR}/" "${MNT_ROOT}/"

    # Boot overlay (if present and p1 mounted)
    if [ -d "${OVERLAY_DIR}/boot" ]; then
      if mountpoint -q "${MNT_BOOT}"; then
        echo "Applying boot overlay from: ${OVERLAY_DIR}/boot -> p1"
        sudo rsync -a "${OVERLAY_DIR}/boot/" "${MNT_BOOT}/"
      else
        echo "WARN: ${OVERLAY_DIR}/boot exists, but boot partition not mounted; skipping boot overlay."
      fi
    fi
  else
    echo "No overlay directory found (tried: filesystem/, src/modules/fullpageos/filesystem/, modules/fullpageos/filesystem/)"
  fi

  # 4) Ensure /host_cache exists (tolerant for CustomPiOS unpack)
  if [ ! -d "${MNT_ROOT}/host_cache" ]; then
    sudo mkdir -p "${MNT_ROOT}/host_cache"
    sudo chmod 0755 "${MNT_ROOT}/host_cache"
  fi

  # 5) (Optional) Normalize ownership for common files (e.g., wait.html)
  if [ -f "${MNT_ROOT}/home/pi/wait.html" ]; then
    sudo chown 1000:1000 "${MNT_ROOT}/home/pi/wait.html" 2>/dev/null || true
  fi

  # 6) (Optional) Run a fast in-chroot updater if your image expects it
  # Bind mounts to make chroot safer for tools that expect /dev,/proc,/sys
  sudo mount --bind /dev  "${MNT_ROOT}/dev"
  sudo mount --bind /proc "${MNT_ROOT}/proc"
  sudo mount --bind /sys  "${MNT_ROOT}/sys"

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

  sync

  # 7) Emit the modified image
  cp -f "${WORK_IMG}" "${IMG_OUT}"
  echo "Wrote overlayed image to: ${IMG_OUT}"
  echo "Could not compute git diff; assuming overlay contains changes and will run in-chroot updater if present"
  CHANGED="overlay_changes_present"
fi

if echo "$CHANGED" | grep -qE 'modules/(klipper|moonraker|crowsnest)|overlay_changes_present'; then
  echo "Detected app/module or overlay changes; running in-chroot fast updater"
  sudo chroot "$WORK/mnt" /bin/bash -lc '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    if [ -x /opt/custompios/scripts/update_apps_fast.sh ]; then
      /opt/custompios/scripts/update_apps_fast.sh
    fi
  '
fi

sync
sudo umount -R "$WORK/mnt"
cp "$IMG_IN" "$IMG_OUT"
