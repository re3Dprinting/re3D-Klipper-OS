#!/bin/bash
set -euo pipefail

echo "Content-Type: text/plain"
echo ""

# Fixed paths
BASE="/home/pi"
PD="$BASE/printer_data"
GCODES="$PD/gcodes"
DB="$PD/database/moonraker-sql.db"
USB="/media/usb0"

# Optional: name=<basename-without-ext>
NAME=""
parse_kv () {
  local kv k v; kv="$1"; k="${kv%%=*}"; v="${kv#*=}"
  v="$(printf '%s' "$v" | sed 's/+/ /g;s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null || true)"
  case "$k" in name) NAME="$v" ;; esac
}
if [ -n "${QUERY_STRING:-}" ]; then
  for kv in ${QUERY_STRING//&/ }; do parse_kv "$kv"; done
fi
if [ "${REQUEST_METHOD:-}" = "POST" ]; then
  BODY="$(dd bs=1 count="${CONTENT_LENGTH:-0}" 2>/dev/null || true)"
  for kv in ${BODY//&/ }; do parse_kv "$kv"; done
fi

# Preflight
if [ ! -d "$GCODES" ]; then
  echo "ERROR: $GCODES not found"; exit 1
fi
if [ ! -d "$USB" ]; then
  echo "ERROR: USB mount $USB not found"; exit 1
fi
if ! mount | grep -q " on ${USB} "; then
  echo "ERROR: $USB is not mounted"; exit 1
fi
# Write test
if ! ( : > "$USB/.write_test.$$" ) 2>/dev/null; then
  echo "ERROR: Cannot write to $USB (permissions or read-only FS)"; exit 1
fi
rm -f "$USB/.write_test.$$"

# Name + paths
ts="$(date +%Y%m%d-%H%M%S)"
base="${NAME:-print_restore_bundle-${ts}}"
final="$USB/${base}.tgz"
tmp="$USB/.${base}.tgz.tmp.$$"

# Build include list
includes=( "printer_data/gcodes" )
if [ -f "$DB" ]; then
  includes+=( "printer_data/database/moonraker-sql.db" )
else
  echo "NOTE: $DB not found; continuing without DB"
fi

# Rough space check: require at least 5 MiB free (tar size unknown until built)
avail_k=$(df -Pk "$USB" | awk 'NR==2{print $4}')
if [ "${avail_k:-0}" -lt 5120 ]; then
  echo "ERROR: Not enough free space on $USB"; exit 1
fi

# Create tarball directly on USB (atomic write)
cd "$BASE"
tar -czf "$tmp" "${includes[@]}"

# Verify non-zero and move into place
sz="$(stat -c%s "$tmp" 2>/dev/null || echo 0)"
if [ "$sz" -le 0 ]; then
  rm -f "$tmp"
  echo "ERROR: Archive creation failed (size 0)"; exit 1
fi
mv -f "$tmp" "$final"
sync

# Final confirmation
fsz="$(stat -c%s "$final" 2>/dev/null || echo 0)"
if [ "$fsz" -ne "$sz" ] || [ "$fsz" -le 0 ]; then
  echo "ERROR: Verification failed (tmp=${sz} bytes, final=${fsz} bytes)"; exit 1
fi

echo "OK: Saved to $final (size: ${fsz} bytes)"