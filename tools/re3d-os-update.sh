#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-devel}"   # passed from bootstrap (not strictly required here)
LOG_TAG="[re3D-OS update]"

REPO_DIR="/opt/re3d-os-src"

# ---------- UI log/status files (live web dir, not repo src) ----------
UI_DIR="/opt/mconfig/www"
STATUS_FILE="${UI_DIR}/update.txt"
LOG_FILE="${UI_DIR}/update_log.txt"
PROGRESS_FILE="${UI_DIR}/update_progress.txt"

# Make sure the dir exists (it should on a running system)
mkdir -p "${UI_DIR}"

# Reset UI files each run
: > "${STATUS_FILE}"
: > "${LOG_FILE}"
: > "${PROGRESS_FILE}"

log() {
  # Log to console AND append to UI log
  echo "$*" | tee -a "${LOG_FILE}"
}

set_status() {
  echo "$1" > "${STATUS_FILE}"
  log "${LOG_TAG} STATE → $1"
}

set_progress() {
  echo "$1" > "${PROGRESS_FILE}"
}

on_error() {
  local code=$1
  local line=$2
  log "${LOG_TAG} ERROR: script failed at line ${line} with exit code ${code}"
  set_status "error"
}
trap 'on_error $? $LINENO' ERR

# ---------- Source / destination paths ----------
# Source locations INSIDE the repo clone on the Pi
SRC_MCONFIG="${REPO_DIR}/src/modules/fullpageos/filesystem/opt/mconfig/www"
SRC_FFF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fff"
SRC_FGF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fgf"

# Target locations on the LIVE system
DST_MCONFIG="/opt/mconfig/www"
DST_FFF="/home/pi/printer_data/config/src/fff"
DST_FGF="/home/pi/printer_data/config/src/fgf"

# ---------- Start ----------
set_status "running"
set_progress 0

log "${LOG_TAG} =========================================="
log "${LOG_TAG} Running real updater from repo"
log "${LOG_TAG} Branch/tag: ${BRANCH}"
log "${LOG_TAG} Repo dir:   ${REPO_DIR}"
log "${LOG_TAG} ------------------------------------------"

# Safety check for repo clone (bootstrap already cloned / checked out devel)
if [ ! -d "${REPO_DIR}/.git" ]; then
  log "${LOG_TAG} ERROR: ${REPO_DIR} is not a git repo. Aborting."
  set_status "error"
  exit 1
fi

# ---------- 0) APT update / upgrade ----------
log "${LOG_TAG} Running apt update / upgrade (this may take a while)..."
set_progress 10

# non-interactive apt so it doesn't hang on prompts
export DEBIAN_FRONTEND=noninteractive

log "${LOG_TAG} apt-get update..."
apt-get update >> "${LOG_FILE}" 2>&1

set_progress 25
log "${LOG_TAG} apt-get upgrade -y..."
apt-get -y upgrade >> "${LOG_FILE}" 2>&1

set_progress 30
log "${LOG_TAG} apt update/upgrade complete."

# ---------- 1) Configurator: /opt/mconfig/www ----------
set_progress 45

if [ -d "${SRC_MCONFIG}" ]; then
  log "${LOG_TAG} Syncing Configurator UI (preserving calibration_data)..."
  # Exact mirror, but do NOT touch calibration_data contents
  rsync -a --delete \
    --exclude 'calibration_data/' \
    "${SRC_MCONFIG}/" \
    "${DST_MCONFIG}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_MCONFIG} not found, skipping Configurator sync."
fi

log "${LOG_TAG} Fixing cgi-bin permissions (best effort)..."
chmod -R 755 "${DST_MCONFIG}/cgi-bin/"*.sh 2>/dev/null || true

# ---------- 2) Klipper configs: FFF ----------
set_progress 60

if [ -d "${SRC_FFF}" ]; then
  log "${LOG_TAG} Syncing FFF configs..."
  # NO --delete here so we don't blow away any local-only configs
  rsync -a \
    "${SRC_FFF}/" \
    "${DST_FFF}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_FFF} not found, skipping FFF configs."
fi

# ---------- 3) Klipper configs: FGF ----------
set_progress 75

if [ -d "${SRC_FGF}" ]; then
  log "${LOG_TAG} Syncing FGF configs..."
  # Same: conservative, no --delete
  rsync -a \
    "${SRC_FGF}/" \
    "${DST_FGF}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_FGF} not found, skipping FGF configs."
fi

# ---------- 4) Reload services ----------
set_progress 90

log "${LOG_TAG} Reloading services (best-effort)..."
systemctl daemon-reload || true

for svc in klipper moonraker mainsail nginx crowsnest; do
  if systemctl list-unit-files | grep -q "^${svc}.service"; then
    log "${LOG_TAG} Restarting ${svc}.service ..."
    systemctl restart "${svc}.service" || true
  else
    log "${LOG_TAG} ${svc}.service not found, skipping."
  fi
done

set_progress 96
log "${LOG_TAG} Services reload complete."

# ---------- 5) Done → reboot ----------
set_progress 100
log "${LOG_TAG} Update complete. Printer will reboot now."
set_status "rebooting"

# Give the UI a moment to read final status/progress
sleep 3

log "${LOG_TAG} Rebooting..."
/sbin/reboot
