#!/bin/sh
# /cgi-bin/collect_and_parse_logs.sh
#
# 1) Collect logs into a temp bundle directory (similar to collect_logs.sh)
# 2) Parse the printer logs for known error patterns
# 3) Return a plain-text summary for the web UI

set -u

# Same paths as collect_logs.sh
PRINTER_LOG_DIR="/home/pi/printer_data/logs"
CHROMIUM_LOG_DIR="/home/pi/.config/chromium"

# Temp workdir for this request
WORKDIR="$(mktemp -d /tmp/logscan.XXXXXX)"
BUNDLE_DIR="${WORKDIR}/bundle"
mkdir -p "$BUNDLE_DIR"

# --- 1. COLLECT LOGS INTO BUNDLE_DIR ---

# 1. printer logs
if [ -d "$PRINTER_LOG_DIR" ]; then
    mkdir -p "$BUNDLE_DIR/printer_data_logs"
    cp -r "$PRINTER_LOG_DIR"/. "$BUNDLE_DIR/printer_data_logs/" 2>/dev/null || true
fi

# 2. chromium logs
if [ -d "$CHROMIUM_LOG_DIR" ]; then
    mkdir -p "$BUNDLE_DIR/chromium"
    find "$CHROMIUM_LOG_DIR" -maxdepth 2 -type f \( -name "*.log" -o -name "Crash*" \) -print0 | \
        xargs -0 -I{} cp "{}" "$BUNDLE_DIR/chromium/" 2>/dev/null || true
fi

# 3. dmesg
dmesg > "$BUNDLE_DIR/dmesg.log" 2>/dev/null || true

# 4. journalctl
journalctl -xe --no-pager > "$BUNDLE_DIR/journalctl.log" 2>/dev/null || \
journalctl --no-pager > "$BUNDLE_DIR/journalctl.log" 2>/dev/null || true

# 5. systemctl
systemctl list-units --all > "$BUNDLE_DIR/systemctl-list.log" 2>/dev/null || true
systemctl status > "$BUNDLE_DIR/systemctl-status.log" 2>/dev/null || true

# --- 2. CHOOSE WHICH LOG TO PARSE ---

PRINTER_BUNDLE_DIR="$BUNDLE_DIR/printer_data_logs"

echo "Content-Type: text/plain"
echo

if [ ! -d "$PRINTER_BUNDLE_DIR" ]; then
    echo "No printer logs collected (expected directory: $PRINTER_BUNDLE_DIR)."
    rm -rf "$WORKDIR"
    exit 0
fi

# pick the newest *.log in the collected printer logs
LOG_FILE="$(ls -1t "$PRINTER_BUNDLE_DIR"/*.log 2>/dev/null | head -n1 || echo "")"

if [ -z "$LOG_FILE" ] || [ ! -f "$LOG_FILE" ]; then
    echo "No log file found in $PRINTER_BUNDLE_DIR (expected something like klippy.log)."
    rm -rf "$WORKDIR"
    exit 0
fi

# --- 3. DEFINE ERROR PATTERNS ---

patterns=$(cat <<'EOF'
thermocouple reader fault
thermocouple out of range
max31856: over/under voltage fault
max31856: thermocouple open fault
shutdown due to webhooks request
move out of range
move exceeds maximum extrusion
no trigger on
got eof
unable to open serial port
not heating at expected rate
timeout with mcu
max31856: cold junction range fault
max31856: thermocouple range fault
max31856: cold junction high fault
max31856: cold junction low fault
max31856: thermocouple high fault
max31856: thermocouple low fault
EOF
)

print_solution() {
  key="$1"
  case "$key" in
    "thermocouple reader fault")
cat <<'EOF'
Look for max31856 [ERROR TYPE] in later errors for detailed solutions.
EOF
    ;;

    "thermocouple out of range")
cat <<'EOF'
It read temperatures that were higher or lower than the allowed range.

Fire Hazard.
Safety Feature, so it should not happen. Check heater wiring.

Machine is now a fire risk, be careful.
EOF
    ;;

    "max31856: over/under voltage fault")
cat <<'EOF'
Check the grounding cables on the trolley plate and the bed plate.

Quick Fix:
1. Turn off the machine.
2. Unplug the yellow thermocouple connectors.
3. Turn the machine back on.
4. Press "Restart" on the touchscreen when booted up.
5. Plug the yellow thermocouple connectors back in.
6. Press "Firmware_Restart" on the touchscreen.
EOF
    ;;

    "max31856: thermocouple open fault")
cat <<'EOF'
The thermocouple wiring is faulty or the thermocouple is bad.

Solution:
Replace thermocouple or check wiring.
EOF
    ;;

    "shutdown due to webhooks request")
cat <<'EOF'
This error occurs when the emergency stop button is pressed in the software.

Solution:
1. Press "Firmware_Restart" to restore normal operation.
EOF
    ;;

    "move out of range")
cat <<'EOF'
The printer attempted to move beyond its allowed range, often due to incorrect movement mode settings.

Solution:
1. Check the console output for the coordinates that caused the error.
2. If the error involves X or Y movement, ensure the axis is set to absolute mode.

   - Relative Mode: G1 X10 moves the X axis +10 from its last position.
   - Absolute Mode: G1 X10 moves the X axis to coordinate (10, 10).

3. To fix the issue, add G90 to your start G-code to enforce absolute positioning.
EOF
    ;;

    "move exceeds maximum extrusion")
cat <<'EOF'
This error is often caused by a slicer issue where an excessive extrusion amount is requested.

Solution:
1. Ensure your start G-code includes G92 E0 before any extrusion commands.
2. Verify that your slicer flow settings are correct.
3. If the problem persists, adjust safety limits in the configuration:
   - Copy /build/fff_extruders.cfg and paste it into standalone.cfg.
   - Increase max_extrude_cross_section and max_extrude_only_distance.
   - Save the changes and restart the printer.
EOF
    ;;

    "no trigger on")
cat <<'EOF'
No trigger on Z after full movement.
The printer tried to home that motor and it never reached the home position.

Solution:
Check the motor and limit switches for the corresponding axis.
EOF
    ;;

    "got eof")
cat <<'EOF'
Check the USB connection between the Raspberry Pi (or host machine) and the mainboard. Over time, vibrations can loosen the connectors.

Solution:
1. Power off the printer.
2. Unplug and replug the USB cable connecting the Raspberry Pi to the mainboard.
3. Power the printer back on.
4. If the issue persists, try using a different USB cable or port.
EOF
    ;;

    "unable to open serial port")
cat <<'EOF'
The serial id on the Archimajor board is not being read properly. So the Raspberry Pi and the board cannot communicate.

Solution:
Check the serial id using "ls /dev/serial/by-id" to see the actual serial id of the Archimajor board.
If no file found: Check USB connection from Raspberry Pi to Archimajor and reboot.
If file found and number is given: Check the config file in the build (_serial.cfg), and then specify the serial from the ls command there.
EOF
    ;;

    "not heating at expected rate")
cat <<'EOF'
Either the heating error parameters are too strict, or the thermocouple is not reading correctly.

Solution:
You can view heating error parameters in the fff/fgf_heaters.cfg file under [verify_heater].
You can modify these parameters in the standalone.cfg file.
EOF
    ;;

    "timeout with mcu")
cat <<'EOF'
GCode was intensive. Try a different gcode file to see if the error persists.
EOF
    ;;

    "max31856: cold junction range fault")
cat <<'EOF'
Export this log and check back with a software engineer.
EOF
    ;;

    "max31856: thermocouple range fault")
cat <<'EOF'
Check the thermocouple wiring and if the error persists, export this log and check back with a software engineer.
EOF
    ;;

    "max31856: cold junction high fault")
cat <<'EOF'
Export this log and check back with a software engineer.
EOF
    ;;

    "max31856: cold junction low fault")
cat <<'EOF'
Export this log and check back with a software engineer.
EOF
    ;;

    "max31856: thermocouple high fault")
cat <<'EOF'
Export this log and check back with a software engineer.
EOF
    ;;

    "max31856: thermocouple low fault")
cat <<'EOF'
Export this log and check back with a software engineer.
EOF
    ;;

    *)
      echo "No canned solution text for this error."
    ;;
  esac
}

# --- 4. SCAN LOG FOR PATTERNS ---

found_any=false
log_basename="$(basename "$LOG_FILE")"

# iterate patterns line by line (keep spaces inside each pattern)
oldIFS=$IFS
IFS='
'
set -- $patterns
IFS=$oldIFS

for pat in "$@"; do
  # skip empty lines
  [ -z "$pat" ] && continue

  if grep -qi -- "$pat" "$LOG_FILE"; then
    if [ "$found_any" = false ]; then
      echo "Errors found in $log_basename:"
      echo
      found_any=true
    fi

    echo "----------------------------------------------------------"
    echo "Error Detected: $pat"
    echo
    echo "Possible Solution:"
    echo
    print_solution "$pat"
    echo
  fi
done

if [ "$found_any" = false ]; then
  echo "No known errors found in $log_basename."
fi

# cleanup
rm -rf "$WORKDIR"
exit 0