#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

step() { echo "[layer-apps] $*"; }

step "Preparing environment"
apt-get update
apt-get install -y --no-install-recommends rsync git curl ca-certificates sudo

# Seed caches if present (mounted earlier to /host_cache)
if [ -d /host_cache ]; then
  step "Seeding /host_cache into /home/pi"
  mkdir -p /home/pi
  for d in klipper moonraker crowsnest; do
    if [ -d "/host_cache/$d" ]; then
      rsync -a --delete "/host_cache/$d/" "/home/pi/$d/"
      chown -R pi:pi "/home/pi/$d" || true
    fi
  done
  if [ -d /host_cache/crowsnest-binaries ]; then
    install -Dm0755 /host_cache/crowsnest-binaries/ustreamer /usr/local/bin/ustreamer 2>/dev/null || true
    install -Dm0755 /host_cache/crowsnest-binaries/camera-streamer /usr/local/bin/camera-streamer 2>/dev/null || true
    install -Dm0755 /host_cache/crowsnest-binaries/crowsnest /usr/local/bin/crowsnest 2>/dev/null || true
  fi
fi

# Prefer running the project's start_chroot_script with LAYER_MODE=apps if available
if [ -x /opt/custompios/modules/fullpageos/start_chroot_script ]; then
  export LAYER_MODE=apps
  /opt/custompios/modules/fullpageos/start_chroot_script || true
elif [ -f /start_chroot_script ]; then
  export LAYER_MODE=apps
  bash /start_chroot_script || true
else
  step "No start_chroot_script found; running minimal app build steps"
  # Example minimal actions; real logic should be placed in the repo script
  if [ -d /home/pi/klipper ]; then
    step "Preparing klippy-env placeholder"
    mkdir -p /home/pi/klippy-env || true
  fi
  if [ -d /home/pi/moonraker ]; then
    step "Preparing moonraker-env placeholder"
    mkdir -p /home/pi/moonraker-env || true
  fi
fi

apt-get clean
rm -rf /var/lib/apt/lists/*
