#!/bin/sh
# BusyBox/ash-friendly CGI to store global calibration parameters.

echo "Content-Type: text/plain"
echo ""

# ---- CONFIG ----------------------------------------------------------------
umask 0002

# ---- HELPERS ---------------------------------------------------------------
urldecode() {
  s=$(printf '%s' "$1" | tr '+' ' ' | sed -r 's/%([0-9A-Fa-f]{2})/\\x\1/g')
  printf '%b' "$s"
}

read_stdin() {
  len="${CONTENT_LENGTH:-}"
  if [ -n "$len" ] && [ "$len" -gt 0 ] 2>/dev/null; then
    dd bs=1 count="$len" 2>/dev/null
  else
    cat
  fi
}

trim() {
  printf "%s" "$1" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

is_number() {
  printf "%s" "$1" | awk 'BEGIN{re="^[0-9]+(\\.[0-9]+)?$"} $0 ~ re {ok=1} END{exit ok?0:1}'
}

# ---- READ POST BODY --------------------------------------------------------
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
EXTRUDER=""

OLD_IFS="$IFS"
IFS='&'; set -- $BODY; IFS="$OLD_IFS"

for kv in "$@"; do
  [ -n "$kv" ] || continue
  key_raw=${kv%%=*}
  val_raw=${kv#*=}
  key=$(urldecode "$key_raw")
  val=$(urldecode "$val_raw")
  case "$key" in
    hotend_temp) HOTEND_TEMP="$(trim "$val")" ;;
    bed_temp)    BED_TEMP="$(trim "$val")" ;;
    machine)     MACHINE="$(trim "$val")" ;;
    extruder)    EXTRUDER="$(trim "$val")" ;;
  esac
done

# ---- VALIDATE --------------------------------------------------------------
is_number "$HOTEND_TEMP" || { echo "Error: hotend_temp must be numeric"; exit 0; }
is_number "$BED_TEMP"    || { echo "Error: bed_temp must be numeric"; exit 0; }
[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }

# ---- PATHS -----------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
mkdir -p "$DATA_DIR"

GLOBALS_FILE="$DATA_DIR/globals.env"

# ---- WRITE FILE ------------------------------------------------------------
{
  echo "# Global calibration parameters"
  echo "HOTEND_TEMP=\"$HOTEND_TEMP\""
  echo "BED_TEMP=\"$BED_TEMP\""
  echo "MACHINE=\"$MACHINE\""
  echo "EXTRUDER=\"$EXTRUDER\""
  echo "UPDATED_AT=\"$(date -Iseconds)\""
} > "$GLOBALS_FILE" || { echo "Error: failed to write $GLOBALS_FILE"; exit 0; }

echo "OK: Saved global calibration parameters."
echo "File: $GLOBALS_FILE"
echo ""
echo "HOTEND_TEMP = $HOTEND_TEMP"
echo "BED_TEMP    = $BED_TEMP"
echo "MACHINE     = $MACHINE"
echo "EXTRUDER    = $EXTRUDER"