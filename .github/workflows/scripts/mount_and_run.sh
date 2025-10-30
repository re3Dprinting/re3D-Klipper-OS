#!/usr/bin/env bash
set -euo pipefail

IMG_IN="$1"
IMG_OUT="$2"
CHROOT_SCRIPT="$3"

WORKDIR="$PWD"
TMPDIR=$(mktemp -d)
WORK_IMG="$TMPDIR/work.img"
LOOPDEV=""
cleanup() {
  set +e
  sudo umount -R "$TMPDIR/mnt" 2>/dev/null || true
  [ -n "$LOOPDEV" ] && sudo losetup -d "$LOOPDEV" 2>/dev/null || true
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

mkdir -p "$TMPDIR/mnt"

# Copy the input image to a working image to avoid mutating the original artifact
cp "$IMG_IN" "$WORK_IMG"

# Attach loop and mount root partition
LOOPDEV="$(sudo losetup -fP --show "$WORK_IMG")"
ROOT_PART="${LOOPDEV}p2"

if [ ! -b "$ROOT_PART" ]; then
  echo "Expected partition ${ROOT_PART} not found; listing ${LOOPDEV}*"
  ls -l ${LOOPDEV}*
  exit 1
fi

sudo mount "$ROOT_PART" "$TMPDIR/mnt"

# Copy in your layer script and any supporting files
sudo mkdir -p "$TMPDIR/mnt/custom-layer"
sudo cp "$PWD/.github/workflows/scripts/${CHROOT_SCRIPT}" "$TMPDIR/mnt/custom-layer/${CHROOT_SCRIPT}"

# Bind mounts for chroot operations
sudo mount --bind /dev  "$TMPDIR/mnt/dev"
sudo mount --bind /proc "$TMPDIR/mnt/proc"
sudo mount --bind /sys  "$TMPDIR/mnt/sys"

# Optionally seed host cache into /host_cache inside chroot (if present in WORKDIR)
if [ -n "${HOST_CACHE:-}" ] && [ -d "${HOST_CACHE}" ]; then
  sudo mkdir -p "$TMPDIR/mnt/host_cache"
  sudo rsync -a --delete "${HOST_CACHE}/" "$TMPDIR/mnt/host_cache/"
fi

# Run the chrooted script
sudo chroot "$TMPDIR/mnt" /bin/bash "/custom-layer/${CHROOT_SCRIPT}"

# Sync and unmount
sync
sudo umount -R "$TMPDIR/mnt"

# Copy the modified working image out
cp "$WORK_IMG" "$IMG_OUT"

# cleanup handled by trap
