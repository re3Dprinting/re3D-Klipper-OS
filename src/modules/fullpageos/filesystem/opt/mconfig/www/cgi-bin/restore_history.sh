#!/bin/bash
# Safely restore Moonraker DB from /media/usb0/moonraker-sql.db*
# - Picks the newest file matching moonraker-sql.db*
# - Verifies it's SQLite (if tools present) and sane size
# - Stops moonraker, backups current DB, atomic replace, restart

set -euo pipefail
echo "Content-Type: text/plain"
echo

PRINTER_USER="pi"
PRINTER_HOME="/home/${PRINTER_USER}"
USB_DIR="/media/usb0"
DST_DIR="${PRINTER_HOME}/printer_data/database"
DST_DB="${DST_DIR}/moonraker-sql.db"
CFG_DIR="${PRINTER_HOME}/printer_data/config"
GCODE_DIR="${PRINTER_HOME}/printer_data/gcodes"
TS=$(date +"%Y%m%d-%H%M%S")
BAK_DB="${DST_DIR}/moonraker-sql.db.bak-${TS}"
TMP_DB="${DST_DIR}/.moonraker-sql.db.new-${TS}"

# Destination path sanity
[ -d "${DST_DIR}" ] || { echo "Error: ${DST_DIR} does not exist."; exit 1; }
[ -d "${CFG_DIR}" ] || { echo "Error: Guard: ${CFG_DIR} missing."; exit 1; }
[ -d "${GCODE_DIR}" ] || { echo "Error: Guard: ${GCODE_DIR} missing."; exit 1; }

# Find newest candidate on USB
if ! findmnt -rn "${USB_DIR}" >/dev/null; then
  echo "Error: ${USB_DIR} is not a mounted filesystem."; exit 1
fi
CANDIDATE=$(ls -1t "${USB_DIR}"/moonraker-sql.db* 2>/dev/null | head -n1 || true)
[ -n "${CANDIDATE}" ] || { echo "Error: No moonraker-sql.db* found on ${USB_DIR}."; exit 1; }

# Size sanity (10KB..200MB)
SZ=$(stat -c%s "${CANDIDATE}")
if [ "${SZ}" -lt 10000 ] || [ "${SZ}" -gt 200000000 ]; then
  echo "Error: Candidate size ${SZ} looks wrong. Refusing."; exit 1
fi

# File(1) & sqlite3 checks if available
if command -v file >/dev/null 2>&1; then
  file "${CANDIDATE}" | grep -qi sqlite || { echo "Error: Not an SQLite file."; exit 1; }
fi
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${CANDIDATE}" 'PRAGMA integrity_check;' | grep -q '^ok$' || { echo "Error: SQLite integrity_check failed."; exit 1; }
fi

# Stop moonraker
sudo systemctl stop moonraker || true

# Backup current DB (if present)
if [ -f "${DST_DB}" ]; then
  sudo cp -f -- "${DST_DB}" "${BAK_DB}"
  echo "Backed up existing DB → ${BAK_DB}"
fi

# Copy to temp, set perms, fsync, atomic move
sudo cp -f -- "${CANDIDATE}" "${TMP_DB}"
sudo chown "${PRINTER_USER}:${PRINTER_USER}" "${TMP_DB}"
sudo chmod 600 "${TMP_DB}"
command -v sync >/dev/null 2>&1 && sudo sync -f "${TMP_DB}" 2>/dev/null || true
sudo mv -f -- "${TMP_DB}" "${DST_DB}"
command -v sync >/dev/null 2>&1 && sudo sync || true

# Start moonraker
sudo systemctl start moonraker

echo "OK: Restored ${CANDIDATE} → ${DST_DB} (size=${SZ}) and restarted Moonraker."
