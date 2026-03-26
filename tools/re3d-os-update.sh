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
SRC_COMMON="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/common"

# Target locations on the LIVE system
DST_MCONFIG="/opt/mconfig/www"
DST_FFF="/home/pi/printer_data/config/src/fff"
DST_FGF="/home/pi/printer_data/config/src/fgf"
DST_COMMON="/home/pi/printer_data/config/src/common"

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

# ---------- 1) Configurator: /opt/mconfig/www ----------
set_progress 10

if [ -d "${SRC_MCONFIG}" ]; then
  log "${LOG_TAG} Syncing Configurator UI (preserving calibration_data)..."
  # Exact mirror, but do NOT touch calibration_data contents
  rsync -a --checksum --delete \
    --exclude 'calibration_data/' \
    "${SRC_MCONFIG}/" \
    "${DST_MCONFIG}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_MCONFIG} not found, skipping Configurator sync."
fi

log "${LOG_TAG} Fixing cgi-bin permissions (best effort)..."
chmod -R 755 "${DST_MCONFIG}/cgi-bin/"*.sh 2>/dev/null || true

# ---------- 2) Klipper configs: FFF ----------
set_progress 20

if [ -d "${SRC_FFF}" ]; then
  log "${LOG_TAG} Syncing FFF configs..."
  # NO --delete here so we don't blow away any local-only configs
  rsync -a --checksum \
    "${SRC_FFF}/" \
    "${DST_FFF}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_FFF} not found, skipping FFF configs."
fi

# ---------- 3) Klipper configs: COMMON ----------
set_progress 30

if [ -d "${SRC_COMMON}" ]; then
  log "${LOG_TAG} Syncing common configs..."
  # Same: conservative, no --delete
  rsync -a --checksum \
    "${SRC_COMMON}/" \
    "${DST_COMMON}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_COMMON} not found, skipping common configs."
fi

# ---------- 4) Klipper configs: FGF ----------
set_progress 40

if [ -d "${SRC_FGF}" ]; then
  log "${LOG_TAG} Syncing FGF configs..."
  # Same: conservative, no --delete
  rsync -a --checksum \
    "${SRC_FGF}/" \
    "${DST_FGF}/"
else
  log "${LOG_TAG} WARNING: Source ${SRC_FGF} not found, skipping FGF configs."
fi

# ---------- 5) Ensure matplotlib is installed for graphstats ----------
set_progress 50
log "${LOG_TAG} Ensuring matplotlib is installed (needed for graph graphs)..."

log "${LOG_TAG} Installing python3-matplotlib if missing..."
apt-get install -y python3-matplotlib || {
  log "${LOG_TAG} ERROR: Failed to install python3-matplotlib"
  set_status "error"
  exit 1
}

log "${LOG_TAG} Verifying matplotlib import..."
if ! python3 -c "import matplotlib" 2>/dev/null; then
  log "${LOG_TAG} ERROR: matplotlib still not importable after install."
  set_status "error"
  exit 1
fi

log "${LOG_TAG} Matplotlib installed successfully."

# Allow git to operate on pi-owned repos when running as root
KLIPPER_DIR="/home/pi/klipper"
MOONRAKER_DIR="/home/pi/moonraker"
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="${KLIPPER_DIR}"
export GIT_CONFIG_KEY_1=safe.directory
export GIT_CONFIG_VALUE_1="${MOONRAKER_DIR}"

# ---------- 6) Klipper: update if not already at latest ----------
set_progress 55

if [ -d "${KLIPPER_DIR}/.git" ]; then
  log "${LOG_TAG} Checking Klipper for updates..."
  cd "${KLIPPER_DIR}"
  git fetch origin 2>&1 | tee -a "${LOG_FILE}" || true
  LOCAL_REV=$(git rev-parse HEAD)
  REMOTE_REV=$(git rev-parse '@{u}' 2>/dev/null || git rev-parse origin/master 2>/dev/null || git rev-parse origin/main)

  if [ "${LOCAL_REV}" = "${REMOTE_REV}" ]; then
    log "${LOG_TAG} Klipper is already up-to-date (${LOCAL_REV:0:8}). Skipping."
  else
    log "${LOG_TAG} Klipper update available (${LOCAL_REV:0:8} → ${REMOTE_REV:0:8}). Updating..."
    log "${LOG_TAG} *** NOTE: Klipper was updated. The Archimajor board firmware must be re-flashed. ***"
    sudo systemctl stop klipper || true
    git pull 2>&1 | tee -a "${LOG_FILE}"
    log "${LOG_TAG} Updating Klipper Python dependencies..."
    /home/pi/klippy-env/bin/pip install -r "${KLIPPER_DIR}/scripts/klippy-requirements.txt" 2>&1 | tee -a "${LOG_FILE}"
    sudo systemctl start klipper || true

    # Trigger the mainboard flash flow on next reboot
    log "${LOG_TAG} Setting firstboot-splash flag for Archimajor board re-flash..."
    touch /etc/firstboot-splash
    systemctl enable flash_once.service 2>/dev/null || true

    log "${LOG_TAG} Klipper updated successfully."
  fi
else
  log "${LOG_TAG} WARNING: ${KLIPPER_DIR} not found or not a git repo. Skipping Klipper update."
fi

# ---------- 7) Moonraker: update if not already at latest ----------
set_progress 65

if [ -d "${MOONRAKER_DIR}/.git" ]; then
  log "${LOG_TAG} Checking Moonraker for updates..."
  cd "${MOONRAKER_DIR}"
  git fetch origin 2>&1 | tee -a "${LOG_FILE}" || true
  LOCAL_REV=$(git rev-parse HEAD)
  REMOTE_REV=$(git rev-parse '@{u}' 2>/dev/null || git rev-parse origin/master 2>/dev/null || git rev-parse origin/main)

  if [ "${LOCAL_REV}" = "${REMOTE_REV}" ]; then
    log "${LOG_TAG} Moonraker is already up-to-date (${LOCAL_REV:0:8}). Skipping."
  else
    log "${LOG_TAG} Moonraker update available (${LOCAL_REV:0:8} → ${REMOTE_REV:0:8}). Updating..."
    sudo systemctl stop moonraker || true
    git pull 2>&1 | tee -a "${LOG_FILE}"
    log "${LOG_TAG} Running Moonraker dependency installer..."
    sudo -u pi "${MOONRAKER_DIR}/scripts/install-moonraker.sh" -r 2>&1 | tee -a "${LOG_FILE}"
    sudo systemctl start moonraker || true
    log "${LOG_TAG} Moonraker updated successfully."
  fi
else
  log "${LOG_TAG} WARNING: ${MOONRAKER_DIR} not found or not a git repo. Skipping Moonraker update."
fi

# ---------- 8) Mainsail: update if not already at latest ----------
set_progress 78

MAINSAIL_DIR="/home/pi/mainsail"
log "${LOG_TAG} Checking Mainsail for updates..."

# Get the latest release tag from GitHub
LATEST_MAINSAIL=$(curl -sS --max-time 15 \
  https://api.github.com/repos/mainsail-crew/mainsail/releases/latest \
  | grep -Po '"tag_name":\s*"\K[^"]+' || true)

if [ -z "${LATEST_MAINSAIL}" ]; then
  log "${LOG_TAG} WARNING: Could not fetch latest Mainsail release tag. Skipping Mainsail update."
else
  CURRENT_MAINSAIL=""
  if [ -f "${MAINSAIL_DIR}/.version" ]; then
    CURRENT_MAINSAIL=$(cat "${MAINSAIL_DIR}/.version" 2>/dev/null || true)
  fi

  if [ "${CURRENT_MAINSAIL}" = "${LATEST_MAINSAIL}" ]; then
    log "${LOG_TAG} Mainsail is already up-to-date (${CURRENT_MAINSAIL}). Skipping."
  else
    log "${LOG_TAG} Mainsail update available (${CURRENT_MAINSAIL:-unknown} → ${LATEST_MAINSAIL}). Updating..."
    mkdir -p "${MAINSAIL_DIR}"
    cd "${MAINSAIL_DIR}"
    rm -rf ./*
    wget -q -O mainsail.zip \
      https://github.com/mainsail-crew/mainsail/releases/latest/download/mainsail.zip
    unzip -o mainsail.zip
    rm -f mainsail.zip
    # Record installed version for future comparison
    echo "${LATEST_MAINSAIL}" > "${MAINSAIL_DIR}/.version"
    log "${LOG_TAG} Mainsail updated to ${LATEST_MAINSAIL} successfully."
  fi
fi

# ---------- 9) Reload services (best-effort, no "not found" noise) ----------
set_progress 90

log "${LOG_TAG} Reloading services (best-effort)..."
systemctl daemon-reload || true

for svc in klipper moonraker mainsail nginx crowsnest; do
  log "${LOG_TAG} Restarting ${svc}.service (best-effort)..."
  systemctl restart "${svc}.service" || true
done

set_progress 96
log "${LOG_TAG} Services reload complete."

# ---------- 10) Done → reboot ----------
set_progress 100
log "${LOG_TAG} Update complete. Printer will reboot now."
set_status "rebooting"

# Give the UI a moment to read final status/progress
sleep 3

log "${LOG_TAG} Rebooting..."
/sbin/reboot
