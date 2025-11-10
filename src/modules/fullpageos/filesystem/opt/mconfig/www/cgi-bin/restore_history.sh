#!/bin/bash
set -euo pipefail

echo "Content-Type: text/plain"
echo ""

# --- Fixed paths ---
BASE="/home/pi"
PD="$BASE/printer_data"
GCODES="$PD/gcodes"
DB="$PD/database/moonraker-sql.db"
USB="/media/usb0"

BUNDLE=""
SRC=""
NAME=""

# ---------- parse args (GET or POST) ----------
decode() { printf '%s' "$1" | sed 's/+/ /g;s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null || true; }
parse_kv () {
  local kv="$1" k="${kv%%=*}" v="${kv#*=}"
  v="$(decode "$v")"
  case "$k" in
    bundle) BUNDLE="$v" ;;
    src)    SRC="$v" ;;
    name)   NAME="$v" ;;
  esac
}
if [ -n "${QUERY_STRING:-}" ]; then
  for kv in ${QUERY_STRING//&/ }; do parse_kv "$kv"; done
fi
if [ "${REQUEST_METHOD:-}" = "POST" ]; then
  read -r BODY || true
  for kv in ${BODY//&/ }; do parse_kv "$kv"; done
fi

# ---------- helpers ----------
pick_newest_any () {
  # newest *.tgz in the given dir
  ls -1t "$1"/*.tgz 2>/dev/null | head -n1
}
list_candidates () {
  ls -1t "$1"/*.tgz 2>/dev/null || true
}

# If only name= was provided, assume /media/usb0/<name>[.tgz]
if [ -z "$BUNDLE" ] && [ -n "$NAME" ]; then
  case "$NAME" in
    *.tgz) BUNDLE="$USB/$NAME" ;;
    *)      BUNDLE="$USB/$NAME.tgz" ;;
  esac
fi

# Default to src=usb if nothing specified
if [ -z "$BUNDLE" ] && [ -z "$SRC" ]; then
  SRC="usb"
fi

# Resolve src=usb|home
if [ -z "$BUNDLE" ]; then
  case "$SRC" in
    usb)
      if [ ! -d "$USB" ] || ! mount | grep -q " on ${USB} "; then
        echo "ERROR: USB at $USB not mounted or not present."; exit 1
      fi
      cand_count=$(list_candidates "$USB" | wc -l | tr -d ' ')
      echo "USB: found ${cand_count} candidate bundle(s)."
      BUNDLE="$(pick_newest_any "$USB")"
      ;;
    home)
      cand_count=$(list_candidates "$BASE" | wc -l | tr -d ' ')
      echo "HOME: found ${cand_count} candidate bundle(s)."
      BUNDLE="$(pick_newest_any "$BASE")"
      ;;
  esac
fi

if [ -z "$BUNDLE" ] || [ ! -f "$BUNDLE" ]; then
  echo "ERROR: Bundle not found."
  echo "Use one of:"
  echo "  • /cgi-bin/restore_history.sh?src=usb            (auto-pick newest on /media/usb0)"
  echo "  • /cgi-bin/restore_history.sh?bundle=/full/path.tgz"
  echo "  • /cgi-bin/restore_history.sh?name=mybundle[.tgz]  (assumes /media/usb0)"
  exit 1
fi

echo "Using bundle: $BUNDLE"

# ---------- unpack ----------
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
echo "Unpacking..."
tar -xzf "$BUNDLE" -C "$tmpdir"

# quick structure check
if [ ! -d "$tmpdir/printer_data" ]; then
  echo "ERROR: Bundle missing printer_data/ root"; exit 1
fi
[ -d "$tmpdir/printer_data/gcodes" ] || echo "NOTE: No gcodes/ in bundle."

# ---------- restore G-codes (merge; no delete) ----------
if [ -d "$tmpdir/printer_data/gcodes" ]; then
  echo "Restoring G-code files → $GCODES ..."
  mkdir -p "$GCODES"
  before_count="$(find "$GCODES" -type f 2>/dev/null | wc -l || echo 0)"
  bundle_count="$(find "$tmpdir/printer_data/gcodes" -type f | wc -l)"
  rsync -a "$tmpdir/printer_data/gcodes/" "$GCODES/"
  chown -R pi:pi "$GCODES" || true
  after_count="$(find "$GCODES" -type f 2>/dev/null | wc -l || echo 0)"
  echo "G-codes merged (bundle=$bundle_count, before=$before_count, after=$after_count)."
fi

# ---------- restore DB (safe swap with backup) ----------
if [ -f "$tmpdir/printer_data/database/moonraker-sql.db" ]; then
  echo "Restoring Moonraker DB…"
  mkdir -p "$(dirname "$DB")"

  was_active=0
  if systemctl is-active --quiet moonraker; then
    was_active=1
    echo "Stopping Moonraker..."
    systemctl stop moonraker || true
  fi

  if [ -f "$DB" ]; then
    ts="$(date +%Y%m%d-%H%M%S)"
    cp -a "$DB" "$DB.bak.$ts"
    echo "Backed up current DB → $DB.bak.$ts"
  fi

  cp -a "$tmpdir/printer_data/database/moonraker-sql.db" "$DB"
  chown pi:pi "$DB" || true

  if [ "$was_active" -eq 1 ]; then
    echo "Starting Moonraker..."
    systemctl start moonraker || true
  fi
  echo "DB restored."
else
  echo "NOTE: No moonraker-sql.db in bundle."
fi

echo "OK: Restore complete from $BUNDLE"