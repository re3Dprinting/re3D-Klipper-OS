#!/bin/sh
# BusyBox/ash-friendly CGI to generate a PA gcode from a template

echo "Content-Type: text/plain"
echo ""

# --- helpers ---
urldecode() {
  # POSIX/BusyBox-safe: + -> space, %HH -> byte
  s=$(printf '%s' "$1" | tr '+' ' ' | sed -r 's/%([0-9A-Fa-f]{2})/\\x\1/g')
  # printf expands \xHH into raw bytes:
  printf '%b' "$s"
}

read_stdin() {
  # CGI: read exactly $CONTENT_LENGTH; manual: read all
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
  # 0 if numeric int/decimal
  printf "%s" "$1" | awk 'BEGIN{re="^[0-9]+(\\.[0-9]+)?$"} $0 ~ re {ok=1} END{exit ok?0:1}'
}

# --- read POST body ---
BODY="$(read_stdin)"

HOTEND_TEMP=""
BED_TEMP=""
MACHINE=""
EXTRUDER=""

# split BODY by '&'
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

# --- validate ---
is_number "$HOTEND_TEMP" || { echo "Error: hotend_temp must be a number (e.g., 220 or 220.5)"; exit 0; }
is_number "$BED_TEMP"    || { echo "Error: bed_temp must be a number (e.g., 60 or 60.0)"; exit 0; }
[ -n "$MACHINE" ]  || { echo "Error: machine is required"; exit 0; }
[ -n "$EXTRUDER" ] || { echo "Error: extruder is required"; exit 0; }

# map extruder to tool index
TOOL_SELECT="0"
case "$EXTRUDER" in
  left|L|Left|T0|0)   TOOL_SELECT="0" ;;
  right|R|Right|T1|1) TOOL_SELECT="1" ;;
  *) TOOL_SELECT="0" ;;
esac

# --- paths ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GCODE_DIR="$WWW_ROOT/gcode"
OUT_DIR="$GCODE_DIR/gen"
mkdir -p "$OUT_DIR"

# choose template per machine
case "$MACHINE" in
  "Gigabot 4")       TEMPLATE="$GCODE_DIR/tmpl.GB4-PA.gcode" ;;
  "Gigabot 4 XLT")   TEMPLATE="$GCODE_DIR/tmpl.GB4XLT-PA.gcode" ;;
  "Terabot 4")       TEMPLATE="$GCODE_DIR/tmpl.T4-PA.gcode" ;;
  *)                 TEMPLATE="$GCODE_DIR/tmpl.GB4-PA.gcode" ;;
esac

if [ ! -f "$TEMPLATE" ]; then
  echo "Error: template not found: $TEMPLATE"
  exit 0
fi

safe_machine="$(echo "$MACHINE" | tr ' ' '_' )"
ts="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$OUT_DIR/PA_${safe_machine}_E${EXTRUDER}_H${HOTEND_TEMP}_B${BED_TEMP}_${ts}.gcode"

# substitute placeholders
sed \
  -e "s/{tool_select}/$TOOL_SELECT/g" \
  -e "s/{bed_temp}/$BED_TEMP/g" \
  -e "s/{hotend_temp}/$HOTEND_TEMP/g" \
  "$TEMPLATE" > "$OUT_FILE"

echo "Generated: $OUT_FILE"
echo "URL: /gcode/gen/$(basename "$OUT_FILE")"