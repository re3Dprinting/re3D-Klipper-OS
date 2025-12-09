#!/bin/sh
# /cgi-bin/parse_log.sh
#
# Scan ALL /home/pi/printer_data/logs/klippy*.log files
# Find known error patterns
# Attach run start + approximate time-in-run for each error (when possible)
# Skip the first "timeout with mcu" and "got eof" in each log
# Deduplicate identical events (same pattern + run start + stats time)
# Output an HTML fragment for the UI

set -u

PRINTER_LOG_DIR="/home/pi/printer_data/logs"

echo "Content-Type: text/html"
echo

if [ ! -d "$PRINTER_LOG_DIR" ]; then
    echo "<div class='log-errors-empty'>Printer log directory not found: ${PRINTER_LOG_DIR}</div>"
    exit 0
fi

# --- ERROR PATTERNS ---

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

# Save patterns to a temp file so we can iterate cleanly
PAT_FILE="$(mktemp /tmp/klip_patterns.XXXXXX)"
printf '%s\n' "$patterns" > "$PAT_FILE"

html_escape() {
  # basic HTML escape for text nodes
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

any_global="false"

for LOG_FILE in "$PRINTER_LOG_DIR"/klippy*.log; do
  [ -f "$LOG_FILE" ] || continue

  log_basename="$(basename "$LOG_FILE")"
  file_has_any="false"

  # Per-file dedupe of (pattern + start + stats)
  SEEN_FILE="$(mktemp /tmp/klip_seen.XXXXXX)"

  # For each pattern, find matching lines + line numbers
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue

    MATCH_FILE="$(mktemp /tmp/klip_matches.XXXXXX)"
    # -a: treat as text even if "binary file matches"
    grep -a -ni -- "$pat" "$LOG_FILE" > "$MATCH_FILE" 2>/dev/null || true

    # Skip the first timeout/got eof per log
    skip_first="false"
    pat_lc="$(printf '%s' "$pat" | tr '[:upper:]' '[:lower:]')"
    case "$pat_lc" in
      "timeout with mcu"|"got eof")
        skip_first="true"
        ;;
    esac
    first_seen="false"

    while IFS=: read -r ln rest; do
      [ -z "$ln" ] && continue

      # Skip first occurrence of timeout/got eof
      if [ "$skip_first" = "true" ] && [ "$first_seen" = "false" ]; then
        first_seen="true"
        continue
      fi

      # Find nearest previous "Start printer at ..." before this line
      start_line="$(
        sed -n "1,${ln}p" "$LOG_FILE" \
        | grep -a 'Start printer at' \
        | tail -n 1
      )"

      start_pretty=""
      if [ -n "$start_line" ]; then
        start_pretty="$(
          printf '%s\n' "$start_line" \
          | sed 's/^Start printer at //; s/ (.*$//'
        )"
      fi

      # Find nearest previous "Stats XXX.X:" before this line
      stats_line="$(
        sed -n "1,${ln}p" "$LOG_FILE" \
        | grep -a '^Stats [0-9.]*:' \
        | tail -n 1
      )"

      stats_secs=""
      if [ -n "$stats_line" ]; then
        stats_secs="$(
          printf '%s\n' "$stats_line" \
          | sed -n 's/^Stats \([0-9.]*\):.*/\1/p'
        )"
      fi

      # Dedupe: if this (pattern + start + stats) combo already printed, skip
      key="${pat}|${start_pretty}|${stats_secs}"
      if grep -Fxq -- "$key" "$SEEN_FILE" 2>/dev/null; then
        continue
      fi
      echo "$key" >> "$SEEN_FILE"

      # First error anywhere → open global wrapper
      if [ "$any_global" = "false" ]; then
        echo "<div class='log-errors-wrap'>"
        any_global="true"
      fi

      # First error in this log → open per-file block
      if [ "$file_has_any" = "false" ]; then
        echo "<div class='log-errors-file'>"
        echo "  <div class='log-file-title'>Errors in <span class='log-file-name'>$(html_escape "$log_basename")</span></div>"
        file_has_any="true"
      fi

      esc_pat="$(html_escape "$pat")"

      # Slug for data-error-type (for filtering in UI)
      safe_type="$(
        printf '%s' "$pat" \
        | tr '[:upper:]' '[:lower:]' \
        | tr -c 'a-z0-9' '-' \
        | sed 's/--*/-/g; s/^-//; s/-$//'
      )"

      # Build meta block
      run_html=""
      offset_html=""
      if [ -n "$start_pretty" ]; then
        run_html="      <div class='log-error-run'>Run started: $(html_escape "$start_pretty")</div>"
      fi

      if [ -n "$stats_secs" ]; then
        off_str="$(format_offset "$stats_secs")"
        if [ -n "$off_str" ]; then
          off_str_esc="$(html_escape "$off_str")"
          offset_html="      <div class='log-error-time'>Approx time in run: ${off_str_esc}</div>"
        fi
      fi

      echo "  <div class='log-error-card' data-error-type='${safe_type}'>"
      echo "    <div class='log-error-header'>"
      echo "      <span class='log-error-pill'>Error</span>"
      echo "      <span class='log-error-name'>$esc_pat</span>"
      echo "    </div>"

      if [ -n "$run_html" ] || [ -n "$offset_html" ]; then
        echo "    <div class='log-error-meta'>"
        [ -n "$run_html" ] && echo "$run_html"
        [ -n "$offset_html" ] && echo "$offset_html"
        echo "    </div>"
      fi

      echo "    <div class='log-error-body'>"
      echo "      <div class='log-error-solution-title'>Suggested fix</div>"
      echo "      <pre class='log-error-solution-text'>"
      print_solution "$pat"
      echo "      </pre>"
      echo "    </div>"
      echo "  </div>"

    done < "$MATCH_FILE"

    rm -f "$MATCH_FILE"
  done < "$PAT_FILE"

  if [ "$file_has_any" = "true" ]; then
    echo "</div>"  # close .log-errors-file
  fi

  rm -f "$SEEN_FILE"
done

if [ "$any_global" = "true" ]; then
  echo "</div>"  # close .log-errors-wrap
else
  echo "<div class='log-errors-empty'>No known errors found in any klippy log.</div>"
fi

rm -f "$PAT_FILE"
exit 0