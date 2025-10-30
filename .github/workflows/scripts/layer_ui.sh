#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

step() { echo "[layer-ui] $*"; }

step "Installing minimal UI deps"
apt-get update
apt-get install -y --no-install-recommends rsync unzip ca-certificates sudo

# Prefer running the project's start_chroot_script with LAYER_MODE=ui if available
if [ -x /opt/custompios/modules/fullpageos/start_chroot_script ]; then
  export LAYER_MODE=ui
  /opt/custompios/modules/fullpageos/start_chroot_script || true
elif [ -f /start_chroot_script ]; then
  export LAYER_MODE=ui
  bash /start_chroot_script || true
else
  step "No start_chroot_script found; running minimal UI config"
  # Fallback: touch a marker
  mkdir -p /var/www/html || true
  echo "ui-layer-applied" >/var/www/html/.layer_marker || true
fi

apt-get clean
rm -rf /var/lib/apt/lists/*
