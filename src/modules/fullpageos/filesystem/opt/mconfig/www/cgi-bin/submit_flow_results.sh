#!/bin/sh
# CGI to record Flow Calibration results (flow multiplier + notes).

echo "Content-Type: text/plain"
echo ""

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

# ---- READ POST BODY --------------------------------------------------------
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
EXTRUDER=""
MODE=""
FLOW=""
NOTES=""

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
    mode)        MODE="$(trim "$val")" ;;
    flow)        FLOW="$(trim "$val")" ;;
    notes)       NOTES="$(trim "$val")" ;;
  esac
done

[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }
[ -n "$FLOW" ]     || { echo "Error: flow is required"; exit 0; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
mkdir -p "$DATA_DIR"

RESULTS_FILE="$DATA_DIR/calibration_results.log"

TS="$(date -Iseconds)"
# Format: timestamp | type | mode | machine | extruder | hotend | bed | value | notes
printf "%s | flow | %s | %s | %s | %s | %s | %s | %s\n" \
  "$TS" "${MODE:-unknown}" "$MACHINE" "$EXTRUDER" "$HOTEND_TEMP" "$BED_TEMP" "$FLOW" "$NOTES" \
  >> "$RESULTS_FILE" || { echo "Error: failed to append to $RESULTS_FILE"; exit 0; }

echo "OK: Saved flow calibration result."
echo "File: $RESULTS_FILE"