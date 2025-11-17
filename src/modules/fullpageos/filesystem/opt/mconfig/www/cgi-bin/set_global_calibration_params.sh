#!/bin/sh
# CGI: save global calibration parameters to calibration_data/globals.env

echo "Content-Type: text/plain"
echo ""

# ---- CONFIG / PATHS --------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
GLOBALS_FILE="$DATA_DIR/globals.env"

mkdir -p "$DATA_DIR"
umask 0002  # cooperative perms

# ---- HELPERS ---------------------------------------------------------------
urldecode() {
  # POSIX/BusyBox-safe: + -> space, %HH -> byte
  s=$(printf '%s' "$1" | tr '+' ' ' | sed -r 's/%([0-9A-Fa-f]{2})/\\x\1/g')
  # interpret \xHH sequences
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

shell_quote() {
  # Safely single-quote an arbitrary string for shell/env files
  # 'foo bar' -> 'foo bar'
  # foo'bar -> 'foo'\''bar'
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

# ---- READ POST BODY --------------------------------------------------------
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
MATERIAL=""
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
    material)    MATERIAL="$(trim "$val")" ;;
    extruder)    EXTRUDER="$(trim "$val")" ;;
  esac
done

# ---- VALIDATE --------------------------------------------------------------
is_number "$HOTEND_TEMP" || { echo "Error: hotend_temp must be numeric"; exit 0; }
is_number "$BED_TEMP"    || { echo "Error: bed_temp must be numeric"; exit 0; }
[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }
# MATERIAL is optional for now; can be enforced later if you want.

# ---- WRITE GLOBALS FILE ----------------------------------------------------
TMP_FILE="$(mktemp "$DATA_DIR/.tmp.globals.XXXXXX")" || {
  echo "Error: mktemp failed in $DATA_DIR"
  exit 0
}

{
  echo "# Global calibration parameters (auto-generated, do not edit by hand)"
  echo "HOTEND_TEMP=$HOTEND_TEMP"
  echo "BED_TEMP=$BED_TEMP"
  printf "MACHINE=%s\n"    "$(shell_quote "$MACHINE")"
  printf "MATERIAL=%s\n"   "$(shell_quote "$MATERIAL")"
  printf "EXTRUDER=%s\n"   "$(shell_quote "$EXTRUDER")"
  printf "UPDATED_AT=%s\n" "$(shell_quote "$(date -Iseconds)")"
} > "$TMP_FILE"

mv -f "$TMP_FILE" "$GLOBALS_FILE" || {
  echo "Error: failed to move temp file into place ($GLOBALS_FILE)"
  rm -f "$TMP_FILE"
  exit 0
}

echo "OK: Saved global calibration parameters."
echo "File: $GLOBALS_FILE"
echo "HOTEND_TEMP=$HOTEND_TEMP"
echo "BED_TEMP=$BED_TEMP"
echo "MACHINE=$MACHINE"
echo "MATERIAL=$MATERIAL"
echo "EXTRUDER=$EXTRUDER"