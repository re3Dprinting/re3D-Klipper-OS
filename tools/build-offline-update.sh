#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ] || [ "$(id -u)" -ne 0 ]; then
  echo "Usage: sudo bash tools/build-offline-update.sh build.img re3d-os-update.tar" >&2
  exit 1
fi

IMAGE="$(realpath "$1")"
OUTPUT="$(realpath -m "$2")"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d /var/tmp/re3d-offline-build.XXXXXX)"
LOOP=""
cleanup() {
  local rc=$?
  trap - EXIT
  if mountpoint -q "${WORK}/root/dev"; then umount -R "${WORK}/root/dev"; fi
  if mountpoint -q "${WORK}/root/proc"; then umount "${WORK}/root/proc"; fi
  if mountpoint -q "${WORK}/root"; then umount "${WORK}/root"; fi
  if mountpoint -q "${WORK}/lower"; then umount "${WORK}/lower"; fi
  if [ -n "${LOOP}" ]; then losetup -d "${LOOP}"; fi
  # WORK is a fresh, private build directory; never delete through a mount.
  if ! mountpoint -q "${WORK}/root" && ! mountpoint -q "${WORK}/lower"; then
    rm -rf -- "${WORK}/upper" "${WORK}/overlay-work"
    rmdir "${WORK}/root" "${WORK}/lower" "${WORK}"
  fi
  exit "${rc}"
}
trap cleanup EXIT

mkdir "${WORK}/lower" "${WORK}/root" "${WORK}/upper" "${WORK}/overlay-work"
LOOP="$(losetup --find --show --partscan --read-only "${IMAGE}")"
udevadm settle
mount -o ro "${LOOP}p2" "${WORK}/lower"
# All apt activity is confined to an overlay, leaving the flashable image unchanged.
mount -t overlay overlay -o "lowerdir=${WORK}/lower,upperdir=${WORK}/upper,workdir=${WORK}/overlay-work" "${WORK}/root"
mount --rbind /dev "${WORK}/root/dev"
mount --make-rslave "${WORK}/root/dev"
mount -t proc proc "${WORK}/root/proc"
rm -f "${WORK}/root/etc/resolv.conf"
cp -L /etc/resolv.conf "${WORK}/root/etc/resolv.conf"

chroot "${WORK}/root" /bin/bash -se <<'PREPARE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C
mkdir -p /var/tmp/re3d-offline-packages/partial
apt-get -o Acquire::Retries=5 -o APT::Update::Error-Mode=any update
declare -A local_names=()
local_packages=()
for package in /var/lib/re3d-offline-packages/*.deb; do
  name="$(dpkg-deb -f "$package" Package)"
  if [ "$(dpkg-deb -f "$package" Version)" != "$(dpkg-query -W -f='${Version}' "$name")" ]; then
    echo "Cached local package does not match the image: $name" >&2
    exit 1
  fi
  local_names["$name"]=1
  cp "$package" /var/tmp/re3d-offline-packages/
  local_packages+=("/var/tmp/re3d-offline-packages/$(basename "$package")")
done
installed_list="$(dpkg-query -W -f='${db:Status-Status} ${binary:Package}\n')"
mapfile -t installed < <(printf '%s\n' "$installed_list" | awk '$1 == "installed" {print $2}')
if [ "${#installed[@]}" -eq 0 ]; then
  echo "Image has no installed-package inventory." >&2
  exit 1
fi
packages=()
for name in "${installed[@]}"; do
  if [ -z "${local_names[${name%%:*}]:-}" ]; then packages+=("$name"); fi
done
# Resolve from an empty package database so installed dependencies are downloaded
# too, rather than producing a bundle that works only on the build machine.
apt-get -y --download-only --no-install-recommends \
  -o Dir::State::status=/dev/null \
  -o Dir::Cache::archives=/var/tmp/re3d-offline-packages \
  -o Acquire::Retries=5 install "${packages[@]}" "${local_packages[@]}"
for venv in klippy-env moonraker-env klipperscreen-env; do
  "/home/pi/${venv}/bin/python" -m pip check
done
/home/pi/klippy-env/bin/python -m pip install --dry-run --no-index -r /home/pi/klipper/scripts/klippy-requirements.txt
grep -vi pillow /home/pi/moonraker/scripts/moonraker-requirements.txt > /var/tmp/re3d-moonraker-requirements.txt
/home/pi/moonraker-env/bin/python -m pip install --dry-run --no-index -r /var/tmp/re3d-moonraker-requirements.txt
/home/pi/klipperscreen-env/bin/python -m pip install --dry-run --no-index -r /home/pi/KlipperScreen/scripts/KlipperScreen-requirements.txt
python3 /opt/mconfig/offline_update.py compatibility > /var/tmp/re3d-offline-compatibility.json
PREPARE

python3 "${TOOLS}/build_offline_update.py" "${WORK}/root" \
  "${WORK}/root/var/tmp/re3d-offline-packages" \
  "${WORK}/root/var/tmp/re3d-offline-compatibility.json" "${OUTPUT}"
