#!/bin/bash
# Safely copy Moonraker history DB to /media/usb0/moonraker-sql.db
# - Verifies USB is a separate mounted filesystem
# - Refuses if USB resolves inside ~/printer_data
# - Copies a timestamped file to avoid accidental overwrite

set -euo pipefail
echo "Content-Type: text/plain"
echo

PRINTER_USER="pi"
PRINTER_HOME="/home/${PRINTER_USER}"
SRC_DB="${PRINTER_HOME}/printer_data/database/moonraker-sql.db"
USB_DIR="/media/usb0"
TS=$(date +"%Y%m%d-%H%M%S")
DST_DB="${USB_DIR}/moonraker-sql.db.${TS}"

# Must exist
[ -f "${SRC_DB}" ] || { echo "Error: Source DB not found at ${SRC_DB}"; exit 1; }

# USB must be a real mount
if ! findmnt -rn "${USB_DIR}" >/dev/null; then
  echo "Error: ${USB_DIR} is not a mounted filesystem."; exit 1
fi

# USB must be on a different device than $PRINTER_HOME to avoid path overlap mistakes
HOME_DEV=$(stat -fc %d "${PRINTER_HOME}")
USB_DEV=$(stat -fc %d "${USB_DIR}")
if [ "${HOME_DEV}" = "${USB_DEV}" ]; then
  echo "Error: ${USB_DIR} is on the same device as ${PRINTER_HOME}. Refusing."; exit 1
fi

# USB path must not resolve inside printer_data
USB_REAL=$(readlink -f "${USB_DIR}")
PD_REAL=$(readlink -f "${PRINTER_HOME}/printer_data")
case "${USB_REAL}" in
  "$PD_REAL"*) echo "Error: ${USB_DIR} resolves inside printer_data. Refusing."; exit 1;;
esac

# Optional: sanity on DB size (10KB..200MB)
SZ=$(stat -c%s "${SRC_DB}")
if [ "${SZ}" -lt 10000 ] || [ "${SZ}" -gt 200000000 ]; then
  echo "Error: DB size ${SZ} looks wrong. Refusing."; exit 1
fi

# Copy with new timestamped filename; keep old copies intact
sudo cp --preserve=timestamps -- "${SRC_DB}" "${DST_DB}"
sudo sync

echo "OK: Saved ${SRC_DB} → ${DST_DB} (${SZ} bytes)."
