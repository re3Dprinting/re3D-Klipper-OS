#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-devel}"   # passed from bootstrap (not strictly required here)
LOG_TAG="[re3D-OS update]"

REPO_DIR="/opt/re3d-os-src"

# Source locations INSIDE the repo clone on the Pi
SRC_MCONFIG="${REPO_DIR}/src/modules/fullpageos/filesystem/opt/mconfig/www"
SRC_FFF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fff"
SRC_FGF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fgf"

# Target locations on the LIVE system
DST_MCONFIG="/opt/mconfig/www"
DST_FFF="/home/pi/printer_data/config/src/fff"
DST_FGF="/home/pi/printer_data/config/src/fgf"

echo "${LOG_TAG} =========================================="
echo "${LOG_TAG} Running real updater from repo"
echo "${LOG_TAG} Branch/tag: ${BRANCH}"
echo "${LOG_TAG} Repo dir:   ${REPO_DIR}"
echo "${LOG_TAG} ------------------------------------------"

# Safety checks for repo clone
if [ ! -d "${REPO_DIR}/.git" ]; then
  echo "${LOG_TAG} ERROR: ${REPO_DIR} is not a git repo. Aborting."
  exit 1
fi

# ----------------- 1) Configurator: /opt/mconfig/www -----------------

if [ -d "${SRC_MCONFIG}" ]; then
  echo "${LOG_TAG} Syncing Configurator UI..."
  # For web assets we usually want an exact mirror
  rsync -a --delete \
    "${SRC_MCONFIG}/" \
    "${DST_MCONFIG}/"
else
  echo "${LOG_TAG} WARNING: Source ${SRC_MCONFIG} not found, skipping Configurator sync."
fi
chmod -R 755 "${DST_MCONFIG}/cgi-bin/"*.sh || true
# -------- 2) Klipper configs: /home/pi/printer_data/.../fff -----------

if [ -d "${SRC_FFF}" ]; then
  echo "${LOG_TAG} Syncing FFF configs..."
  # NO --delete here so we don't blow away any local-only configs
  rsync -a \
    "${SRC_FFF}/" \
    "${DST_FFF}/"
else
  echo "${LOG_TAG} WARNING: Source ${SRC_FFF} not found, skipping FFF configs."
fi

# -------- 3) Klipper configs: /home/pi/printer_data/.../fgf -----------

if [ -d "${SRC_FGF}" ]; then
  echo "${LOG_TAG} Syncing FGF configs..."
  # Same: conservative, no --delete
  rsync -a \
    "${SRC_FGF}/" \
    "${DST_FGF}/"
else
  echo "${LOG_TAG} WARNING: Source ${SRC_FGF} not found, skipping FGF configs."
fi

# ----------------- 4) Reload services (simple) ----------------

echo "${LOG_TAG} Reloading services (best-effort)..."
systemctl daemon-reload || true

for svc in klipper moonraker mainsail nginx crowsnest; do
  if systemctl list-unit-files | grep -q "^${svc}.service"; then
    echo "${LOG_TAG} Restarting ${svc}.service ..."
    systemctl restart "${svc}.service" || true
  fi
done

echo "${LOG_TAG} Update complete."
