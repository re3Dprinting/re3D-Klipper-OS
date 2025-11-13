#!/bin/sh
# CGI: expose saved global calibration parameters to the UI.

echo "Content-Type: text/plain"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
GLOBALS_FILE="$DATA_DIR/globals.env"

if [ ! -f "$GLOBALS_FILE" ]; then
  echo "# No globals file found at $GLOBALS_FILE"
  exit 0
fi

# Read the file in a subshell to avoid polluting environment
(
  # shellcheck disable=SC1090
  . "$GLOBALS_FILE" 2>/dev/null || true
  echo "HOTEND_TEMP=${HOTEND_TEMP:-}"
  echo "BED_TEMP=${BED_TEMP:-}"
  echo "MACHINE=${MACHINE:-}"
  echo "EXTRUDER=${EXTRUDER:-}"
  echo "UPDATED_AT=${UPDATED_AT:-}"
)
