#!/bin/bash
# /opt/mconfig/www/cgi-bin/analyze_gcode.sh
# CGI: Scans a G-code file and returns layer count, max Z, and file size as JSON.
#
# Query params:
#   file=<filename relative to /home/pi/printer_data/gcodes/>
#
# Response: application/json
#   {"layers": N, "max_z": Z, "file_size": S}
#   or on error: {"error": "..."}

echo "Content-Type: application/json"
echo ""

GCODES="/home/pi/printer_data/gcodes"

# ── Parse query string ──────────────────────────────────────────────────────
FILE_PARAM=""
parse_kv() {
  local kv="$1" k v
  k="${kv%%=*}"
  v="${kv#*=}"
  # URL-decode: + → space, %XX → char
  v="$(printf '%s' "$v" | sed 's/+/ /g; s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null || printf '%s' "$v")"
  case "$k" in
    file) FILE_PARAM="$v" ;;
  esac
}

IFS='&' read -ra KVS <<< "${QUERY_STRING:-}"
for kv in "${KVS[@]}"; do parse_kv "$kv"; done

# ── Validate ────────────────────────────────────────────────────────────────
if [[ -z "$FILE_PARAM" ]]; then
  printf '{"error":"Missing parameter: file"}\n'
  exit 0
fi

# Security: reject path traversal
CLEAN="${FILE_PARAM//\.\.\//}"
CLEAN="${CLEAN//\.\.\\/}"
if [[ "$CLEAN" != "$FILE_PARAM" ]] || [[ "$FILE_PARAM" == /* ]]; then
  printf '{"error":"Invalid file path"}\n'
  exit 0
fi

FULL_PATH="$GCODES/$CLEAN"
if [[ ! -f "$FULL_PATH" ]]; then
  printf '{"error":"File not found: %s"}\n' "$CLEAN"
  exit 0
fi

# ── Scan ────────────────────────────────────────────────────────────────────
FILE_SIZE="$(stat -c%s "$FULL_PATH" 2>/dev/null || echo 0)"

# Count layer changes (lines matching ';LAYER:N' or ';LAYER_CHANGE' or ';layer ...')
# Also handles OrcaSlicer '; layer' comments and Cura/PrusaSlicer ';LAYER:' markers.
LAYER_COUNT="$(grep -cE '^\s*;(LAYER[: _]|layer_change|LAYER_CHANGE)' "$FULL_PATH" 2>/dev/null || echo 0)"

# Find maximum Z value from G0/G1 Z moves
MAX_Z="$(grep -oP '(?<=\bZ)[\d]+\.?[\d]*' "$FULL_PATH" 2>/dev/null \
  | awk 'BEGIN{m=0} {v=$1+0; if(v>m) m=v} END{printf "%.3f", m}')"

# Remove trailing zeros after decimal for cleanliness (e.g. 12.000 → 12, 12.400 → 12.4)
MAX_Z="$(printf '%s' "$MAX_Z" | sed 's/\.0\+$//; s/\(\.[0-9]*[1-9]\)0\+$/\1/')"

printf '{"layers":%d,"max_z":"%s","file_size":%s}\n' \
  "${LAYER_COUNT:-0}" "${MAX_Z:-0}" "${FILE_SIZE:-0}"
