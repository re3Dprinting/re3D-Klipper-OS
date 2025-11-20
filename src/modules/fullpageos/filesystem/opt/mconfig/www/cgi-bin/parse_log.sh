#!/bin/sh
# /cgi-bin/parse_log.sh
#
# 1) Scan ALL /home/pi/printer_data/logs/klippy*.log files
# 2) Look for known error patterns
# 3) Return an HTML fragment for the web UI
#    – like the original working version
#    – but skip the *first* "timeout with mcu" and "got eof" per file

set -u

PRINTER_LOG_DIR="/home/pi/printer_data/logs"

echo "Content-Type: text/html"
echo

if [ ! -d "$PRINTER_LOG_DIR" ]; then
    echo "<div class='log-errors-empty'>Printer log directory not found: ${PRINTER_LOG_DIR}</div>"
    exit 0
fi

# --- 1. DEFINE ERROR PATTERNS ---

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

html_escape() {
  # basic HTML escape
  printf '%s' "$1" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'
}

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
1. Power off the machine.
2. Unplug and replug the USB cable connecting the Raspberry Pi to the mainboard.
3. Power the machine back on.
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

# --- 2. SCAN *ALL* KLIPPY LOGS DIRECTLY IN PRINTER_LOG_DIR ---

any_global="false"

for LOG_FILE in "$PRINTER_LOG_DIR"/klippy*.log; do
  [ -f "$LOG_FILE" ] || continue

  found_any="false"
  log_basename="$(basename "$LOG_FILE")"

  # split patterns into $1, $2, ...
  oldIFS=$IFS
  IFS='
'
  set -- $patterns
  IFS=$oldIFS

  for pat in "$@"; do
    [ -z "$pat" ] && continue

    # Decide how many occurrences we require in THIS FILE:
    # - For "timeout with mcu" and "got eof": require at least 2 (skip the first)
    # - For all others: require at least 1
    case "$pat" in
      "timeout with mcu"|"got eof")
        min_count=2
        ;;
      *)
        min_count=1
        ;;
    esac

    # Count matches (case-insensitive) and force treating file as text (-a)
    count=$(grep -a -i -- "$pat" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')

    # If not enough matches, skip this pattern for this file
    if [ -z "$count" ] || [ "$count" -lt "$min_count" ]; then
      continue
    fi

    # At this point, pattern is considered "present" for this file
    if [ "$found_any" = "false" ]; then
      if [ "$any_global" = "false" ]; then
        echo "<div class='log-errors-wrap'>"
      fi
      any_global="true"

      echo "<div class='log-errors-file'>"
      echo "  <div class='log-file-title'>Errors in <span class='log-file-name'>$(html_escape "$log_basename")</span></div>"
      found_any="true"
    fi

    esc_pat="$(html_escape "$pat")"
    echo "  <div class='log-error-card'>"
    echo "    <div class='log-error-header'>"
    echo "      <span class='log-error-pill'>Error</span>"
    echo "      <span class='log-error-name'>$esc_pat</span>"
    echo "    </div>"
    echo "    <div class='log-error-body'>"
    echo "      <div class='log-error-solution-title'>Suggested fix</div>"
      # Pre tag preserves newlines nicely
    echo "      <pre class='log-error-solution-text'>"
    print_solution "$pat"
    echo "      </pre>"
    echo "    </div>"
    echo "  </div>"
  done

  if [ "$found_any" = "true" ]; then
    echo "</div>"  # close .log-errors-file
  fi
done

if [ "$any_global" = "true" ]; then
  echo "</div>"  # close .log-errors-wrap
else
  echo "<div class='log-errors-empty'>No known errors found in any klippy log.</div>"
fi

exit 0