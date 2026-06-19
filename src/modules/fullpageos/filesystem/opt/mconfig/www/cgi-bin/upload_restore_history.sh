#!/bin/bash
set -euo pipefail
# CGI – accepts a raw binary .tgz POST and restores print history.
# The browser client should POST with Content-Type: application/octet-stream.

echo "Content-Type: text/plain"
echo ""

if [ "${REQUEST_METHOD:-}" != "POST" ]; then
  echo "ERROR: POST required"; exit 1
fi

CL="${CONTENT_LENGTH:-0}"
if [ "${CL:-0}" -le 0 ]; then
  echo "ERROR: Empty upload (Content-Length=${CL})"; exit 1
fi

# 500 MiB safety cap
MAX_BYTES=$((500 * 1024 * 1024))
if [ "$CL" -gt "$MAX_BYTES" ]; then
  echo "ERROR: Upload exceeds 500 MiB limit"; exit 1
fi

tmpfile="$(mktemp /tmp/bundle-upload-XXXXXX.tgz)"
trap 'rm -f "$tmpfile"' EXIT

echo "Receiving ${CL} bytes…"
# head -c reads exactly N bytes from stdin (works for binary data)
head -c "$CL" > "$tmpfile"

actual="$(stat -c%s "$tmpfile" 2>/dev/null || echo 0)"
if [ "${actual:-0}" -le 0 ]; then
  echo "ERROR: Upload empty after receive"; exit 1
fi

# Validate archive integrity
if ! tar -tzf "$tmpfile" > /dev/null 2>&1; then
  echo "ERROR: Not a valid .tgz archive"; exit 1
fi

# Confirm expected bundle structure
if ! tar -tzf "$tmpfile" | grep -q "^printer_data/"; then
  echo "ERROR: Bundle missing printer_data/ root"; exit 1
fi

echo "Bundle validated (${actual} bytes). Restoring…"

# --- Restore ---
BASE="/home/pi"
PD="$BASE/printer_data"
GCODES="$PD/gcodes"
DB="$PD/database/moonraker-sql.db"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"; rm -f "$tmpfile"' EXIT

tar -xzf "$tmpfile" -C "$tmpdir"

if [ ! -d "$tmpdir/printer_data" ]; then
  echo "ERROR: Bundle missing printer_data/ root after extract"; exit 1
fi
[ -d "$tmpdir/printer_data/gcodes" ] || echo "NOTE: No gcodes/ in bundle."

# Restore G-code files (merge; no delete)
if [ -d "$tmpdir/printer_data/gcodes" ]; then
  echo "Restoring G-code files → $GCODES …"
  mkdir -p "$GCODES"
  before_count="$(find "$GCODES" -type f 2>/dev/null | wc -l || echo 0)"
  bundle_count="$(find "$tmpdir/printer_data/gcodes" -type f | wc -l)"
  rsync -a "$tmpdir/printer_data/gcodes/" "$GCODES/"
  chown -R pi:pi "$GCODES" || true
  after_count="$(find "$GCODES" -type f 2>/dev/null | wc -l || echo 0)"
  echo "G-codes merged (bundle=${bundle_count}, before=${before_count}, after=${after_count})."
fi

# Restore Moonraker DB (safe swap with backup)
if [ -f "$tmpdir/printer_data/database/moonraker-sql.db" ]; then
  echo "Restoring Moonraker DB…"
  mkdir -p "$(dirname "$DB")"

  was_active=0
  if systemctl is-active --quiet moonraker 2>/dev/null; then
    was_active=1
    echo "Stopping Moonraker…"
    systemctl stop moonraker || true
  fi

  if [ -f "$DB" ]; then
    ts_bak="$(date +%Y%m%d-%H%M%S)"
    cp -a "$DB" "$DB.bak.${ts_bak}"
    echo "Backed up current DB → $DB.bak.${ts_bak}"
  fi

  cp -a "$tmpdir/printer_data/database/moonraker-sql.db" "$DB"
  chown pi:pi "$DB" || true

  if [ "$was_active" -eq 1 ]; then
    echo "Starting Moonraker…"
    systemctl start moonraker || true
  fi
  echo "DB restored."
else
  echo "NOTE: No moonraker-sql.db in bundle."
fi

echo "OK: Restore from upload complete."
