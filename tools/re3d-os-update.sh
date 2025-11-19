#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-devel}"
LOG_TAG="[re3D-OS update]"

REPO_DIR="/opt/re3d-os-src"

# ---------------- RUNTIME STATE (NOT IN GIT) ----------------
STATE_DIR="/tmp/re3d-os-update"
STATUS_FILE="${STATE_DIR}/status"
LOG_FILE="${STATE_DIR}/log"
PROGRESS_FILE="${STATE_DIR}/progress"

mkdir -p "$STATE_DIR"

# Clean old state files
rm -f "$STATUS_FILE" "$LOG_FILE" "$PROGRESS_FILE" || true
touch "$STATUS_FILE" "$LOG_FILE" "$PROGRESS_FILE"

log() {
    echo "$*" | tee -a "$LOG_FILE"
}

set_status() {
    echo "$1" > "$STATUS_FILE"
    log "STATE → $1"
}

set_progress() {
    echo "$1" > "$PROGRESS_FILE"
}

# Initial state
set_status "running"
set_progress 0
log "==========================================="
log "Starting Updater (branch: $BRANCH)"
log "Repo location: $REPO_DIR"
log "==========================================="

# ---------------- SOURCE LOCATIONS ----------------
SRC_MCONFIG="${REPO_DIR}/src/modules/fullpageos/filesystem/opt/mconfig/www"
SRC_FFF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fff"
SRC_FGF="${REPO_DIR}/src/modules/fullpageos/filesystem/home/pi/printer_data/config/src/fgf}"

# ---------------- DEST LOCATIONS ------------------
DST_MCONFIG="/opt/mconfig/www"
DST_FFF="/home/pi/printer_data/config/src/fff"
DST_FGF="/home/pi/printer_data/config/src/fgf"

# SAFETY CHECK
if [ ! -d "${REPO_DIR}/.git" ]; then
  log "ERROR: Repo directory missing .git"
  set_status "error"
  exit 1
fi

# --------------------------------------------------
# Step runner with progress + logging
# --------------------------------------------------
run_step() {
    local pct="$1"
    shift
    local msg="$*"

    log ""
    log "--- $msg ---"
    set_progress "$pct"

    if ! eval "$@"; then
        log "ERROR during: $msg"
        set_status "error"
        exit 1
    fi
}

# --------------------------------------------------
# 1) Prepare repo
# --------------------------------------------------
run_step 5  "Fetching repo updates" \
"git -C \"$REPO_DIR\" fetch --all --prune"

run_step 10 "Resetting repo to origin/$BRANCH" \
"git -C \"$REPO_DIR\" reset --hard origin/$BRANCH"

# --------------------------------------------------
# 2) Sync Configurator (preserving calibration_data)
# --------------------------------------------------
if [ -d "${SRC_MCONFIG}" ]; then
  run_step 30 "Syncing Configurator UI" \
  "rsync -a --delete --exclude 'calibration_data/' \"$SRC_MCONFIG/\" \"$DST_MCONFIG/\""
else
  log "WARNING: SRC_MCONFIG not found. Skipping."
fi

run_step 35 "Fixing cgi-bin permissions" \
"chmod -R 755 \"$DST_MCONFIG/cgi-bin\"/*.sh || true"

# --------------------------------------------------
# 3) Sync FFF configs
# --------------------------------------------------
if [ -d "${SRC_FFF}" ]; then
  run_step 50 "Syncing FFF configs" \
  "rsync -a \"$SRC_FFF/\" \"$DST_FFF/\""
else
  log "WARNING: SRC_FFF not found."
fi

# --------------------------------------------------
# 4) Sync FGF configs
# --------------------------------------------------
if [ -d "${SRC_FGF}" ]; then
  run_step 60 "Syncing FGF configs" \
  "rsync -a \"$SRC_FGF/\" \"$DST_FGF/\""
else
  log "WARNING: SRC_FGF not found."
fi

# --------------------------------------------------
# 5) Restart services
# --------------------------------------------------
run_step 70 "Reloading systemd" \
"systemctl daemon-reload || true"

SERVICES="klipper moonraker mainsail nginx crowsnest"
pct=72

for svc in $SERVICES; do
  run_step $pct "Restarting $svc.service" \
  "systemctl restart \"$svc.service\" || true"
  pct=$((pct + 5))
done

set_progress 95
log "All services restarted."

# --------------------------------------------------
# 6) Mark complete → reboot
# --------------------------------------------------
log ""
log "===== UPDATE COMPLETE — REBOOTING ====="
set_progress 100
set_status "rebooting"

# Give UI a moment to read final state (via CGI readers)
sleep 3

log "Rebooting now..."
/sbin/reboot
