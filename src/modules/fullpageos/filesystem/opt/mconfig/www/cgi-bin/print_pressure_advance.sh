#!/bin/sh
# BusyBox/ash-friendly CGI to generate a Pressure Advance G-code from a template
# and auto-print the most recent generated file via Moonraker.

echo "Content-Type: text/plain"
echo ""

# ---- CONFIG ----------------------------------------------------------------
# Toggle auto-print (1 = enabled, 0 = disabled)
AUTO_PRINT="${AUTO_PRINT:-1}"

# Moonraker URL and optional API key (override via environment)
MOONRAKER_URL="${MOONRAKER_URL:-http://localhost:7125}"
API_KEY="${API_KEY:-}"   # e.g., export API_KEY="your-moonraker-key"

# Cooperative perms for files we create (group-writable)
umask 0002

# ---- HELPERS ---------------------------------------------------------------
urldecode() {
  # POSIX/BusyBox-safe: + -> space, %HH -> byte
  s=$(printf '%s' "$1" | tr '+' ' ' | sed -r 's/%([0-9A-Fa-f]{2})/\\x\1/g')
  printf '%b' "$s"
}

read_stdin() {
  # CGI: read exactly $CONTENT_LENGTH bytes; manual testing: read all
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
  # 0 if numeric (int or decimal), else 1
  printf "%s" "$1" | awk 'BEGIN{re="^[0-9]+(\\.[0-9]+)?$"} $0 ~ re {ok=1} END{exit ok?0:1}'
}

# ---- READ POST BODY --------------------------------------------------------
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
EXTRUDER=""

# Split BODY by '&', then each pair by '=' (ash-safe)
OLD_IFS="$IFS"
IFS='&'; set -- $BODY; IFS="$OLD_IFS"

for kv in "$@"; do
  [ -n "$kv" ] || continue
  key_raw=${kv%%=*}
  val_raw=${kv#*=}
  key=$(urldecode "$key_raw")
  val=$(urldecode "$val_raw")
  [ "${DEBUG:-0}" = "1" ] && echo "DEBUG kv: [$kv] -> key=[$key] val=[$val]"
  case "$key" in
    hotend_temp) HOTEND_TEMP="$(trim "$val")" ;;
    bed_temp)    BED_TEMP="$(trim "$val")" ;;
    machine)     MACHINE="$(trim "$val")" ;;
    extruder)    EXTRUDER="$(trim "$val")" ;;
  esac
done

if [ "${DEBUG:-0}" = "1" ]; then
  echo "DEBUG raw BODY: [$BODY]"
  echo "DEBUG parsed: hotend_temp=[$HOTEND_TEMP] bed_temp=[$BED_TEMP] machine=[$MACHINE] extruder=[$EXTRUDER]"
fi

# ---- VALIDATE --------------------------------------------------------------
is_number "$HOTEND_TEMP" || { echo "Error: hotend_temp must be a number (e.g., 220 or 220.5)"; exit 0; }
is_number "$BED_TEMP"    || { echo "Error: bed_temp must be a number (e.g., 60 or 60.0)"; exit 0; }
[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }

# map extruder to tool index (adjust if your firmware differs)
TOOL_SELECT="0"
case "$EXTRUDER" in
  left|L|Left|T0|0)   TOOL_SELECT="0" ;;
  right|R|Right|T1|1) TOOL_SELECT="1" ;;
  *) TOOL_SELECT="0" ;;
esac

# ---- PATHS -----------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GCODE_DIR="$WWW_ROOT/gcode"
OUT_DIR="$GCODE_DIR/gen"
mkdir -p "$OUT_DIR"

# ---- TEMPLATE SELECTION (matches your filenames) ---------------------------
case "$MACHINE" in
  "Gigabot 4")
    TEMPLATE="$GCODE_DIR/tmpl.GB4-PA.gcode"
    ;;
  "Gigabot 4 XLT")
    TEMPLATE="$GCODE_DIR/tmpl.GB4XLT.gcode"
    ;;
  "Terabot 4")
    TEMPLATE="$GCODE_DIR/tmpl.TB4-PA.gcode"
    ;;
  *)
    TEMPLATE="$GCODE_DIR/tmpl.GB4-PA.gcode" # sane fallback
    ;;
esac

if [ ! -f "$TEMPLATE" ]; then
  echo "Error: template not found: $TEMPLATE"
  exit 0
fi

# Quick guard to help catch wrong template contents early
if ! grep -q '{hotend_temp}' "$TEMPLATE" || ! grep -q '{bed_temp}' "$TEMPLATE" || ! grep -q '{tool_select}' "$TEMPLATE"; then
  echo "Error: template missing one or more placeholders: {tool_select} {bed_temp} {hotend_temp}"
  echo "Template: $TEMPLATE"
  exit 0
fi

# ---- GENERATE OUTPUT FILE --------------------------------------------------
# sanitize machine name: only alphanumeric, everything else -> underscore
safe_machine="$(printf '%s' "$MACHINE" | sed 's/[^A-Za-z0-9]/_/g')"
ts="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$OUT_DIR/PA_${safe_machine}_E${EXTRUDER}_H${HOTEND_TEMP}_B${BED_TEMP}_${ts}.gcode"

# atomic-ish write: mktemp then move
TMP_FILE="$(mktemp "$OUT_DIR/.tmp.PA.XXXXXX")" || { echo "Error: mktemp failed in $OUT_DIR"; exit 0; }

sed \
  -e "s/{tool_select}/$TOOL_SELECT/g" \
  -e "s/{bed_temp}/$BED_TEMP/g" \
  -e "s/{hotend_temp}/$HOTEND_TEMP/g" \
  "$TEMPLATE" > "$TMP_FILE" || { echo "Error: failed to write temp file"; rm -f "$TMP_FILE"; exit 0; }

mv -f "$TMP_FILE" "$OUT_FILE" || { echo "Error: failed to move temp file into place (permissions?)"; rm -f "$TMP_FILE"; exit 0; }

echo "Generated: $OUT_FILE"
echo "URL: /gcode/gen/$(basename "$OUT_FILE")"

# ---- FIND MOST RECENT GENERATED FILE --------------------------------------
# (Use newest by mtime from OUT_DIR in case multiple exist)
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

# Upload to Moonraker (goes into virtual_sdcard/gcodes root by default)
UPLOAD_URL="$MOONRAKER_URL/server/files/upload"
START_URL="$MOONRAKER_URL/printer/print/start"

# Some servers dislike Expect: 100-continue; drop it.
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
