#!/bin/sh
# /cgi-bin/graphstats.sh
#
# Runs Klipper's graphstats.py on a saved log and streams the PNG to the browser.

set -u

GRAPH_SCRIPT="/home/pi/klipper/scripts/graphstats.py"
SAVED_LOG_DIR="/home/pi/saved_logs"
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

# If no log name provided, use newest file in /home/pi/saved_logs
if [ -z "$LOG" ]; then
  LOG="$(ls -1t "$SAVED_LOG_DIR" 2>/dev/null | head -n1 || true)"
fi

if [ -z "$LOG" ] || [ ! -f "$SAVED_LOG_DIR/$LOG" ]; then
  echo "Content-Type: text/plain"
  echo
  echo "No log file found in $SAVED_LOG_DIR."
  exit 0
fi

# Map UI types to graphstats flags
FLAGS=""
case "$TYPE" in
  ""|"bandwidth")
    # mcu bandwidth & load utilization (default)
    FLAGS=""
    ;;
  "freq")
    # mcu frequency
    FLAGS="-f"
    ;;
  "system")
    # system load
    FLAGS="-s"
    ;;
  "heater")
    # heater temperature
    FLAGS="-t HEATER"
    ;;
  *)
    FLAGS=""
    ;;
esac

# Run graphstats.py
# Command matches your pattern:
#   graphstats.py /home/pi/saved_logs/{{Log}} {{Type}} -o /home/pi/graph.png
/usr/bin/python3 "$GRAPH_SCRIPT" "$SAVED_LOG_DIR/$LOG" $FLAGS -o "$TMP_PNG" 2>/tmp/graphstats.err || {
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