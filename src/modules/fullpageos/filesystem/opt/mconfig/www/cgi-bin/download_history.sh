#!/bin/bash
# CGI – builds the print-history bundle and streams it as a browser download.
# All error handling happens BEFORE any headers are written so a proper
# text/plain error response can still be returned on failure.

BASE="/home/pi"
PD="$BASE/printer_data"
GCODES="$PD/gcodes"
DB="$PD/database/moonraker-sql.db"

err_response() {
  printf 'Content-Type: text/plain\r\nStatus: 500 Internal Server Error\r\n\r\n'
  printf 'ERROR: %s\n' "$1"
  exit 1
}

# Preflight
[ -d "$GCODES" ] || err_response "gcodes directory not found at $GCODES"

# Build include list
includes=("printer_data/gcodes")
[ -f "$DB" ] && includes+=("printer_data/database/moonraker-sql.db")

ts="$(date +%Y%m%d-%H%M%S)"
fname="print_restore_bundle-${ts}.tgz"
tmpfile="$(mktemp /tmp/bundle-XXXXXX.tgz)"
trap 'rm -f "$tmpfile"' EXIT

# Create archive
cd "$BASE"
tar -czf "$tmpfile" "${includes[@]}" 2>/dev/null \
  || err_response "Failed to create archive"

sz="$(stat -c%s "$tmpfile" 2>/dev/null || echo 0)"
[ "${sz:-0}" -gt 0 ] || err_response "Archive is empty"

# Stream download response (CRLF line endings per CGI/HTTP spec)
printf 'Content-Type: application/gzip\r\n'
printf 'Content-Disposition: attachment; filename="%s"\r\n' "$fname"
printf 'Content-Length: %s\r\n' "$sz"
printf '\r\n'
cat "$tmpfile"
