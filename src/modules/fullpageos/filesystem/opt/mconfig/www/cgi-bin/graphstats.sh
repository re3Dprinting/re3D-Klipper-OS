#!/bin/sh
# /cgi-bin/graphstats.sh
#
# Runs Klipper's graphstats.py on a log and streams the PNG to the browser.

set -u

GRAPH_SCRIPT="/home/pi/klipper/scripts/graphstats.py"
LOG_DIR="/home/pi/printer_data/logs"
TMP_PNG="/tmp/graphstats.png"

TYPE=""
LOG=""

# Very small query parser: supports ?type=...&log=...
if [ -n "${QUERY_STRING:-}" ]; then
  OLDIFS=$IFS
  IFS='&'
  for kv in $QUERY_STRING; do
    key="${kv%%=*}"
    val="${kv#*=}"
    # extremely minimal decode for spaces; extend if needed
    val="$(printf '%s' "$val" | sed 's/%20/ /g')"
    [ "$key" = "type" ] && TYPE="$val"
    [ "$key" = "log" ]  && LOG="$val"
  done
  IFS=$OLDIFS
fi

# If no log name provided, pick newest klippy*.log in LOG_DIR
if [ -z "$LOG" ]; then
  LOG="$(ls -1t "$LOG_DIR"/klippy*.log 2>/dev/null | head -n1 || true)"
fi

if [ -z "$LOG" ] || [ ! -f "$LOG" ]; then
  echo "Content-Type: text/plain"
  echo
  echo "No klippy log file found in $LOG_DIR (looked for klippy*.log)."
  exit 0
fi

# Map UI types to graphstats flags
FLAGS=""
case "$TYPE" in
  ""|"bandwidth")
    # MCU bandwidth & load utilization (default)
    FLAGS=""
    ;;
  "freq")
    # MCU frequency
    FLAGS="-f"
    ;;
  "system")
    # System load
    FLAGS="-s"
    ;;
  "heater")
    # Heater temperature
    FLAGS="-t HEATER"
    ;;
  *)
    FLAGS=""
    ;;
esac

# Run graphstats.py
# graphstats.py /home/pi/printer_data/logs/klippy.log {{flags}} -o /tmp/graphstats.png
/usr/bin/python3 "$GRAPH_SCRIPT" "$LOG" $FLAGS -o "$TMP_PNG" 2>/tmp/graphstats.err || {
  echo "Content-Type: text/plain"
  echo
  echo "Error running graphstats.py"
  echo
  cat /tmp/graphstats.err 2>/dev/null || true
  exit 0
}

# Stream PNG to browser
echo "Content-Type: image/png"
echo
cat "$TMP_PNG" 2>/dev/null || true
rm -f "$TMP_PNG" 2>/dev/null || true
exit 0