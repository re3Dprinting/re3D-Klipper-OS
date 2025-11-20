#!/bin/sh
# /cgi-bin/parse_log.sh
#
# Parse ALL /home/pi/printer_data/logs/klippy*.log files
# for known error patterns and return an HTML fragment
# for the web UI, with timing info per error.
#
# Also:
#   - Skip the FIRST "timeout with mcu"
#   - Skip the FIRST "got eof"

set -u

PRINTER_LOG_DIR="/home/pi/printer_data/logs"

echo "Content-Type: text/html"
echo

if [ ! -d "$PRINTER_LOG_DIR" ]; then
    echo "<div class='log-errors-empty'>No printer logs directory found at ${PRINTER_LOG_DIR}.</div>"
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

# Helper: format "Stats seconds" as t = XXXs (≈ hh mm ss after start)
format_offset() {
  secs="$1"
  [ -z "$secs" ] && return
  awk -v s="$secs" 'BEGIN{
    h = int(s/3600);
    m = int((s - h*3600)/60);
    sec = s - h*3600 - m*60;
    printf("t = %.1fs (≈ %02dh %02dm %04.1fs after start)", s, h, m, sec);
  }'
}

# Turn patterns into $1, $2, ... for easy looping
oldIFS=$IFS
IFS='
'
set -- $patterns
IFS=$oldIFS

any_global="false"

# Skip the *first* occurrence of these noisy patterns globally
skip_timeout_with_mcu="true"
skip_got_eof="true"

# --- 2. SCAN *ALL* K L I P P Y  LOGS DIRECTLY ---

have_any_log="false"

for LOG_FILE in "$PRINTER_LOG_DIR"/klippy*.log; do
  [ -f "$LOG_FILE" ] || continue
  have_any_log="true"

  file_has_any="false"
  log_basename="$(basename "$LOG_FILE")"

  current_start_pretty=""
  current_stats_secs=""

  # Read log line-by-line so we can track Start/Stats context
  while IFS= read -r line; do
    # Track "Start printer at ..." lines
    case "$line" in
      Start\ printer\ at*)
        # Human-readable part: strip prefix and trailing "(...)" if present
        current_start_pretty="$(printf '%s\n' "$line" | sed 's/^Start printer at //; s/ (.*//')"
        ;;
      Stats\ *:*)
        # Stats 2141.9: ...
        secs="$(printf '%s\n' "$line" | sed -n 's/^Stats \([0-9.]*\):.*/\1/p')"
        [ -n "$secs" ] && current_stats_secs="$secs"
        ;;
    esac

    # Now check this line against all patterns (case-insensitive)
    for pat in "$@"; do
      [ -z "$pat" ] && continue

      # Case-insensitive contains check
      printf '%s\n' "$line" | grep -qi -- "$pat" || continue

      # Skip the first "timeout with mcu"
      if [ "$pat" = "timeout with mcu" ] && [ "$skip_timeout_with_mcu" = "true" ]; then
        skip_timeout_with_mcu="false"
        # do not render this one, just skip
        break  # stop checking other patterns for this line
      fi

      # Skip the first "got eof"
      if [ "$pat" = "got eof" ] && [ "$skip_got_eof" = "true" ]; then
        skip_got_eof="false"
        # do not render this one, just skip
        break  # stop checking other patterns for this line
      fi

      # First error in ANY log → open wrapper
      if [ "$any_global" = "false" ]; then
        echo "<div class='log-errors-wrap'>"
        any_global="true"
      fi

      # First error in THIS log → open file block
      if [ "$file_has_any" = "false" ]; then
        echo "<div class='log-errors-file'>"
        echo "  <div class='log-file-title'>Errors in <span class='log-file-name'>$(html_escape "$log_basename")</span></div>"
        file_has_any="true"
      fi

      esc_pat="$(html_escape "$pat")"

      # Compute offset text if we have Stats seconds
      offset_line=""
      if [ -n "$current_stats_secs" ]; then
        off_str="$(format_offset "$current_stats_secs")"
        if [ -n "$off_str" ]; then
          # HTML-escape the whole formatted string
          off_str_esc="$(html_escape "$off_str")"
          offset_line="<div class='log-error-time'>Approx time in run: ${off_str_esc}</div>"
        fi
      fi

      echo "  <div class='log-error-card'>"
      echo "    <div class='log-error-header'>"
      echo "      <span class='log-error-pill'>Error</span>"
      echo "      <span class='log-error-name'>$esc_pat</span>"
      echo "    </div>"

      # Meta block: run start + time in run (if available)
      if [ -n "$current_start_pretty" ] || [ -n "$offset_line" ]; then
        echo "    <div class='log-error-meta'>"
        if [ -n "$current_start_pretty" ]; then
          echo "      <div class='log-error-run'>Run started: $(html_escape "$current_start_pretty")</div>"
        fi
        if [ -n "$offset_line" ]; then
          echo "      $offset_line"
        fi
        echo "    </div>"
      fi

      echo "    <div class='log-error-body'>"
      echo "      <div class='log-error-solution-title'>Suggested fix</div>"
      echo "      <pre class='log-error-solution-text'>"
      print_solution "$pat"
      echo "      </pre>"
      echo "    </div>"
      echo "  </div>"

      # If a line matches a pattern, we don't want to render duplicates
      # for the same line even if another pattern also matches.
      break
    done
  done < "$LOG_FILE"

  if [ "$file_has_any" = "true" ]; then
    echo "</div>"  # close .log-errors-file
  fi
done

if [ "$any_global" = "true" ]; then
  echo "</div>"  # close .log-errors-wrap
else
  if [ "$have_any_log" = "false" ]; then
    echo "<div class='log-errors-empty'>No klippy logs found in ${PRINTER_LOG_DIR}.</div>"
  else
    echo "<div class='log-errors-empty'>No known errors found in any klippy log.</div>"
  fi
fi

exit 0