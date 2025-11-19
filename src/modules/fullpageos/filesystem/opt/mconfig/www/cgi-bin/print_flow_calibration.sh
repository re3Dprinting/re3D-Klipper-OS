#!/bin/sh
# CGI to generate a Flow Calibration G-code from a template
# and auto-print the most recent generated file via Moonraker.

echo "Content-Type: text/plain"
echo ""

# ---- CONFIG ----------------------------------------------------------------
AUTO_PRINT="${AUTO_PRINT:-1}"
MOONRAKER_URL="${MOONRAKER_URL:-http://localhost:7125}"
API_KEY="${API_KEY:-}"
umask 0002

# ---- HELPERS ---------------------------------------------------------------
urldecode() {
  # POSIX/BusyBox-safe: + -> space, %HH -> byte
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

load_global_from_file() {
  key="$1"
  file="$2"
  [ -f "$file" ] || { printf ""; return; }
  val=$(grep "^$key=" "$file" 2>/dev/null | head -n1 | cut -d= -f2-)
  # strip surrounding single/double quotes if present
  val=$(printf "%s" "$val" | sed "s/^'//; s/'\$//; s/^\"//; s/\"\$//")
  printf "%s" "$val"
}

# ---- READ POST BODY --------------------------------------------------------
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
EXTRUDER=""
MODE=""

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
  esac
done

# ---- PATHS (needed for globals fallback) -----------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GCODE_DIR="$WWW_ROOT/gcode"
OUT_DIR="$GCODE_DIR/gen"
DATA_DIR="$WWW_ROOT/calibration_data"
GLOBALS_FILE="$DATA_DIR/globals.env"

mkdir -p "$OUT_DIR"

# ---- FALLBACK FROM GLOBALS FILE (if POST is missing fields) ----------------
[ -z "$HOTEND_TEMP" ] && HOTEND_TEMP="$(load_global_from_file HOTEND_TEMP "$GLOBALS_FILE")"
[ -z "$BED_TEMP"   ] && BED_TEMP="$(load_global_from_file BED_TEMP "$GLOBALS_FILE")"
[ -z "$MACHINE"    ] && MACHINE="$(load_global_from_file MACHINE "$GLOBALS_FILE")"
[ -z "$EXTRUDER"   ] && EXTRUDER="$(load_global_from_file EXTRUDER "$GLOBALS_FILE")"

# ---- VALIDATE --------------------------------------------------------------
is_number "$HOTEND_TEMP" || { echo "Error: hotend_temp must be a number"; exit 0; }
is_number "$BED_TEMP"    || { echo "Error: bed_temp must be a number"; exit 0; }
[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }

TOOL_SELECT="0"
case "$EXTRUDER" in
  left|L|Left|T0|0)   TOOL_SELECT="0" ;;
  right|R|Right|T1|1) TOOL_SELECT="1" ;;
  *)                  TOOL_SELECT="0" ;;
esac

# ---- TEMPLATE SELECTION ----------------------------------------------------
case "$MACHINE" in
  "Gigabot 4")
    TEMPLATE="$GCODE_DIR/tmpl.GB4-Flow.gcode"
    ;;
  "Gigabot 4 XLT")
    TEMPLATE="$GCODE_DIR/tmpl.GB4XLT-Flow.gcode"
    ;;
  "Terabot 4")
    TEMPLATE="$GCODE_DIR/tmpl.TB4-Flow.gcode"
    ;;
  *)
    TEMPLATE="$GCODE_DIR/tmpl.GB4-Flow.gcode"
    ;;
esac

if [ ! -f "$TEMPLATE" ]; then
  echo "Error: template not found: $TEMPLATE"
  exit 0
fi

if ! grep -q '{hotend_temp}' "$TEMPLATE" || \
   ! grep -q '{bed_temp}' "$TEMPLATE" || \
   ! grep -q '{tool_select}' "$TEMPLATE"; then
  echo "Error: template missing one or more placeholders: {tool_select} {bed_temp} {hotend_temp}"
  echo "Template: $TEMPLATE"
  exit 0
fi

# ---- GENERATE OUTPUT FILE --------------------------------------------------
# sanitize machine name: only alphanumeric, everything else -> underscore
safe_machine="$(printf '%s' "$MACHINE" | sed 's/[^A-Za-z0-9]/_/g')"
ts="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$OUT_DIR/FLOW_${safe_machine}_E${EXTRUDER}_H${HOTEND_TEMP}_B${BED_TEMP}_${ts}.gcode"

TMP_FILE="$(mktemp "$OUT_DIR/.tmp.FLOW.XXXXXX")" || {
  echo "Error: mktemp failed in $OUT_DIR"
  exit 0
}

sed \
  -e "s/{tool_select}/$TOOL_SELECT/g" \
  -e "s/{bed_temp}/$BED_TEMP/g" \
  -e "s/{hotend_temp}/$HOTEND_TEMP/g" \
  "$TEMPLATE" > "$TMP_FILE" || {
    echo "Error: failed to write temp file"
    rm -f "$TMP_FILE"
    exit 0
  }

mv -f "$TMP_FILE" "$OUT_FILE" || {
  echo "Error: failed to move temp file into place"
  rm -f "$TMP_FILE"
  exit 0
}

echo "Generated: $OUT_FILE"
echo "URL: /gcode/gen/$(basename "$OUT_FILE")"

# ---- FIND MOST RECENT GENERATED FILE ---------------------------------------
LATEST="$(ls -t "$OUT_DIR"/*.gcode 2>/dev/null | head -n1)"

if [ -z "$LATEST" ]; then
  echo "Error: no generated files found in $OUT_DIR"
  exit 0
fi

echo "Latest: $LATEST"

# ---- AUTO-PRINT VIA MOONRAKER ---------------------------------------------
if [ "$AUTO_PRINT" != "1" ]; then
  echo "Auto-print disabled (AUTO_PRINT=$AUTO_PRINT). Done."
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl not found; cannot upload/start print."
  exit 0
fi

BASENAME="$(basename "$LATEST")"
UPLOAD_URL="$MOONRAKER_URL/server/files/upload"
START_URL="$MOONRAKER_URL/printer/print/start"

API_KEY_HDR=""
[ -n "$API_KEY" ] && API_KEY_HDR="-H X-Api-Key: $API_KEY"

echo "Uploading to Moonraker: $UPLOAD_URL"
UPLOAD_RES="$(curl -sS -X POST $API_KEY_HDR -H 'Expect:' -F "file=@$LATEST" "$UPLOAD_URL" 2>&1)"
UPLOAD_RC=$?
echo "Upload response: $UPLOAD_RES"
[ $UPLOAD_RC -eq 0 ] || { echo "Error: upload failed ($UPLOAD_RC)"; exit 0; }

# JSON-escape backslashes and quotes in the filename
JSON_FILENAME="$(printf '%s' "$BASENAME" | sed 's/\\/\\\\/g; s/\"/\\\"/g')"
JSON_PAYLOAD="{\"filename\":\"$JSON_FILENAME\"}"

echo "Starting print: $START_URL  (filename=$JSON_FILENAME)"
START_RES="$(printf '%s' "$JSON_PAYLOAD" | curl -sS -X POST $API_KEY_HDR -H 'Content-Type: application/json' --data-binary @- "$START_URL" 2>&1)"
START_RC=$?
echo "Start response: $START_RES"
[ $START_RC -eq 0 ] || { echo "Error: start print failed ($START_RC)"; exit 0; }

echo "OK: Uploaded and started $BASENAME"
