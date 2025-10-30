#!/usr/bin/env bash
set -euo pipefail

IMG_IN="$1"
IMG_OUT="$2"

WORK=$(mktemp -d)
LOOP=""
cleanup() {
  set +e
  sudo umount -R "$WORK/mnt" 2>/dev/null || true
  [ -n "$LOOP" ] && sudo losetup -d "$LOOP" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/mnt"
LOOP="$(sudo losetup -fP --show "$IMG_IN")"
sudo mount "${LOOP}p2" "$WORK/mnt"

# Apply filesystem overlays from repo
if [ -d filesystem ]; then
  sudo rsync -a filesystem/ "$WORK/mnt/"
fi

# Ensure /host_cache exists inside the mounted image so unpack helpers don't fail
if [ ! -d "$WORK/mnt/host_cache" ]; then
  echo "No host_cache in overlay; creating empty /host_cache inside image to be tolerant"
  sudo mkdir -p "$WORK/mnt/host_cache"
  sudo chmod 0755 "$WORK/mnt/host_cache"
fi

# Detect changed files via git if available; fallback to always skip
CHANGED="$(git diff --name-only HEAD~1..HEAD || true)"
if echo "$CHANGED" | grep -qE 'modules/(klipper|moonraker|crowsnest)'; then
  echo "Detected app module changes; running in-chroot fast updater"
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
