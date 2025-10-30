#!/usr/bin/env bash
set -euo pipefail

# Minimal packages-only pass for chrooted image
export DEBIAN_FRONTEND=noninteractive

step() { echo "[layer-packages] $*"; }

step "Updating apt and installing minimal tooling"
apt-get update
apt-get install -y --no-install-recommends rsync ca-certificates sudo

step "Running packages segment of start_chroot_script if present"
# Prefer the repository-provided start_chroot_script if present
if [ -x /opt/custompios/modules/fullpageos/start_chroot_script ]; then
  export LAYER_MODE=packages
  /opt/custompios/modules/fullpageos/start_chroot_script || true
elif [ -f /start_chroot_script ]; then
  export LAYER_MODE=packages
  bash /start_chroot_script || true
else
  step "No start_chroot_script found in expected locations; running minimal package ops"
  # Fallback minimal package installs (adjust as needed)
  apt-get -y --force-yes install git screen avahi-daemon xdotool vim
fi

apt-get clean
rm -rf /var/lib/apt/lists/*
