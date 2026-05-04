#!/bin/bash
# /opt/mconfig/www/cgi-bin/resume_from_layer.sh
# CGI: Generates a reCover_*.gcode file that resumes a print from either:
#   (a) a known byte position (snapshot-based recovery), or
#   (b) the first G1 Z move >= a target Z height (layer-based recovery).
#
# Query params (GET):
#   file=<filename relative to /home/pi/printer_data/gcodes/>
#   position=<byte offset>       — snapshot mode: slice at this byte offset
#   z=<mm>                       — z-based mode: slice at the first layer >= this Z
#   speed=<mm/min>               — optional last known speed to inject
#   outname=<filename>           — optional custom output filename (without path)
#
# Behaviour:
#   - If 'position' is given, slice the file at that byte offset.
#   - If only 'z' is given, scan the file for the first "G1 Z<N>" (or "G0 Z<N>") line
#     where N >= z, then slice just before that line.
#   - Writes a header block extracted from the original file (lines up to and
#     including '; HEADER_BLOCK_END' from OrcaSlicer, or first 50 lines as fallback).
#   - Injects SET_KINEMATIC_POSITION Z=<last_z> (declares position to Klipper, no
#     movement), a 2 mm relative lift to clear the nozzle, speed restore, extruder
#     reset, then the tail of the file.
#   - Saves original as <file>.backup (non-destructive).
#
# Output: text/plain progress messages. Errors begin with "ERROR:".

set -uo pipefail

echo "Content-Type: text/plain"
echo ""

GCODES="/home/pi/printer_data/gcodes"

# ── Parse query string ──────────────────────────────────────────────────────
FILE_PARAM=""
POSITION=""
Z_TARGET=""
SPEED=""
OUTNAME=""
HOTEND_TEMP=""
BED_TEMP=""
X_POS=""
Y_POS=""

parse_kv() {
  local kv="$1" k v
  k="${kv%%=*}"
  v="${kv#*=}"
  v="$(printf '%s' "$v" | sed 's/+/ /g; s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null || printf '%s' "$v")"
  case "$k" in
    file)     FILE_PARAM="$v"  ;;
    position) POSITION="$v"    ;;
    z)        Z_TARGET="$v"    ;;
    speed)    SPEED="$v"       ;;
    outname)  OUTNAME="$v"     ;;
    hotend)   HOTEND_TEMP="$v" ;;
    bed)      BED_TEMP="$v"    ;;
    x)        X_POS="$v"       ;;
    y)        Y_POS="$v"       ;;
  esac
}

IFS='&' read -ra KVS <<< "${QUERY_STRING:-}"
for kv in "${KVS[@]}"; do [[ -n "$kv" ]] && parse_kv "$kv"; done

# ── Validate ────────────────────────────────────────────────────────────────
if [[ -z "$FILE_PARAM" ]]; then
  echo "ERROR: Missing parameter: file"
  exit 0
fi

# Reject path traversal
CLEAN="${FILE_PARAM//\.\.\//}"
CLEAN="${CLEAN//\.\.\\/}"
if [[ "$CLEAN" != "$FILE_PARAM" ]] || [[ "$FILE_PARAM" == /* ]]; then
  echo "ERROR: Invalid file path"
  exit 0
fi

FULL_PATH="$GCODES/$CLEAN"
if [[ ! -f "$FULL_PATH" ]]; then
  echo "ERROR: File not found: $CLEAN"
  exit 0
fi

if [[ -z "$POSITION" && -z "$Z_TARGET" ]]; then
  echo "ERROR: Provide either 'position' (byte offset) or 'z' (target Z height in mm)"
  exit 0
fi

FILE_SIZE="$(stat -c%s "$FULL_PATH")"
echo "Source file : $CLEAN"
echo "File size   : $FILE_SIZE bytes"

# ── Auto-detect temperatures from file if not supplied ──────────────────────
# Primary scan: forward from byte 0 to POSITION (or file end), keeping the LAST
# non-zero M104/M109/M140/M190 seen before the interruption point.
# Fallback: if bed temp is still not found (e.g. slice is before M140 in the
# start sequence), scan the entire file for the FIRST non-zero M140/M190.
# Regex handles optional tool index and other params before S (e.g. M104 T0 S215).
if [[ -z "$HOTEND_TEMP" || -z "$BED_TEMP" ]]; then
  read -r _DETECTED_HOTEND _DETECTED_BED < <(python3 - "$FULL_PATH" "${POSITION:-$FILE_SIZE}" "$FILE_SIZE" <<'PYEOF'
import sys, re

path       = sys.argv[1]
limit      = int(sys.argv[2])
file_size  = int(sys.argv[3])

# Match M104/M109/M140/M190 with S anywhere on the line (handles T0, spaces, etc.)
hotend_re = re.compile(r'^M10[49]\b[^\n]*\bS([1-9][0-9]*)', re.IGNORECASE)
bed_re    = re.compile(r'^M1[49]0\b[^\n]*\bS([1-9][0-9]*)', re.IGNORECASE)

last_hotend = ''
last_bed    = ''
offset = 0
with open(path, 'rb') as fh:
    for raw_line in fh:
        if offset >= limit:
            break
        line = raw_line.decode('utf-8', errors='replace')
        mh = hotend_re.match(line)
        mb = bed_re.match(line)
        if mh: last_hotend = mh.group(1)
        if mb: last_bed    = mb.group(1)
        offset += len(raw_line)

# Fallback: bed temp not found before slice point — scan full file for first hit
if not last_bed and limit < file_size:
    with open(path, 'rb') as fh:
        for raw_line in fh:
            line = raw_line.decode('utf-8', errors='replace')
            mb = bed_re.match(line)
            if mb:
                last_bed = mb.group(1)
                break

print(last_hotend, last_bed)
PYEOF
  )
  [[ -z "$HOTEND_TEMP" && -n "$_DETECTED_HOTEND" ]] && HOTEND_TEMP="$_DETECTED_HOTEND"
  [[ -z "$BED_TEMP"    && -n "$_DETECTED_BED"    ]] && BED_TEMP="$_DETECTED_BED"
fi
[[ -n "$HOTEND_TEMP" ]] && echo "Hotend temp : ${HOTEND_TEMP}°C (last executed)"
[[ -n "$BED_TEMP"    ]] && echo "Bed temp    : ${BED_TEMP}°C (last executed)"

# ── Determine slice byte offset ─────────────────────────────────────────────
SLICE_BYTE=""

if [[ -n "$POSITION" ]]; then
  # Snapshot mode — use the supplied byte offset
  if ! [[ "$POSITION" =~ ^[0-9]+$ ]]; then
    echo "ERROR: position must be a non-negative integer"
    exit 0
  fi
  if [[ "$POSITION" -ge "$FILE_SIZE" ]]; then
    echo "ERROR: position ($POSITION) is >= file size ($FILE_SIZE)"
    exit 0
  fi
  SLICE_BYTE="$POSITION"
  echo "Mode        : snapshot (byte offset)"
  echo "Slice at    : byte $SLICE_BYTE"
else
  # Z-based mode — find the first G0/G1 Z move >= Z_TARGET
  if ! echo "$Z_TARGET" | grep -qE '^[0-9]+(\.[0-9]+)?$'; then
    echo "ERROR: z must be a non-negative number"
    exit 0
  fi
  echo "Mode        : Z-based (target Z >= ${Z_TARGET} mm)"
  echo "Scanning for layer..."

  # Use python3 for reliable byte-offset scanning (awk cannot report byte offsets easily)
  SLICE_BYTE="$(python3 - "$FULL_PATH" "$Z_TARGET" <<'PYEOF'
import sys, re

path   = sys.argv[1]
z_tgt  = float(sys.argv[2])
z_re   = re.compile(r'^(?:G0|G1)\s[^;]*Z([\d]+\.?[\d]*)', re.IGNORECASE)

offset = 0
with open(path, 'rb') as fh:
    for raw_line in fh:
        line = raw_line.decode('utf-8', errors='replace')
        m = z_re.match(line)
        if m:
            z_val = float(m.group(1))
            if z_val >= z_tgt:
                print(offset)
                sys.exit(0)
        offset += len(raw_line)

# Not found
print(-1)
PYEOF
)"

  if [[ -z "$SLICE_BYTE" || "$SLICE_BYTE" == "-1" ]]; then
    echo "ERROR: No G0/G1 Z move >= ${Z_TARGET} mm found in file"
    exit 0
  fi
  echo "Slice at    : byte $SLICE_BYTE (first Z >= ${Z_TARGET} mm)"
fi

# ── Find exact resume line ───────────────────────────────────────────────────
# When toolhead XY is known (snapshot mode), scan backward from file_position
# and find the last G0/G1 line whose parsed coordinates are within 0.1 mm of
# the reported toolhead position.  This gives a ±1-line accurate resume point
# because gcode_move.position is the physical position while file_position is
# bytes-read-ahead (queued but not yet executed).
#
# If XY is unavailable (Z-based mode), fall back to rewinding 100 motion lines.
RAW_SLICE_BYTE="$SLICE_BYTE"

if [[ -n "$X_POS" && -n "$Y_POS" ]]; then
  SLICE_BYTE="$(python3 - "$FULL_PATH" "$RAW_SLICE_BYTE" "$X_POS" "$Y_POS" <<'PYEOF'
import sys, re, math

path  = sys.argv[1]
limit = int(sys.argv[2])
tx    = float(sys.argv[3])
ty    = float(sys.argv[4])

move_re = re.compile(rb'^(?:G0|G1)\b', re.IGNORECASE)
x_re    = re.compile(r'X([-\d.]+)', re.IGNORECASE)
y_re    = re.compile(r'Y([-\d.]+)', re.IGNORECASE)

# Forward pass: collect (byte_offset, interpolated_x, interpolated_y) for every
# G0/G1 line, carrying forward the last known coordinate when an axis is omitted.
lines  = []   # (byte_offset, x, y)
cur_x  = None
cur_y  = None
offset = 0

with open(path, 'rb') as fh:
    for raw_line in fh:
        if offset >= limit:
            break
        if move_re.match(raw_line.lstrip()):
            line = raw_line.decode('utf-8', errors='replace')
            mx = x_re.search(line)
            my = y_re.search(line)
            if mx: cur_x = float(mx.group(1))
            if my: cur_y = float(my.group(1))
            if cur_x is not None and cur_y is not None:
                lines.append((offset, cur_x, cur_y))
        offset += len(raw_line)

if not lines:
    print(limit)
    sys.exit(0)

# Find the last line within 0.1 mm (exact match window).
# Fall back to nearest within 2 mm, then to 100-line rewind.
TOLERANCE = 0.1
FALLBACK  = 2.0

exact_off = None   # last offset with dist <= TOLERANCE (nearest to file_position)
best_off  = lines[0][0]
best_dist = float('inf')

for off, lx, ly in lines:
    dist = math.sqrt((lx - tx)**2 + (ly - ty)**2)
    if dist <= TOLERANCE:
        exact_off = off      # keep overwriting → last occurrence wins
    if dist < best_dist:
        best_dist = dist
        best_off  = off

if exact_off is not None:
    print(exact_off)
elif best_dist <= FALLBACK:
    print(best_off)
else:
    # Nothing close — rewind 100 motion lines as safety net
    idx = max(0, len(lines) - 100)
    print(lines[idx][0])
PYEOF
  )"
  echo "Resume      : XY-matched → byte $SLICE_BYTE (toolhead X=${X_POS} Y=${Y_POS}, was $RAW_SLICE_BYTE)"
else
  # No XY available (Z-based mode) — rewind 100 motion lines
  SLICE_BYTE="$(python3 - "$FULL_PATH" "$RAW_SLICE_BYTE" <<'PYEOF'
import sys, re

path    = sys.argv[1]
limit   = int(sys.argv[2])
rewind  = 100
move_re = re.compile(rb'^(?:G0|G1|G2|G3)\b', re.IGNORECASE)

offsets = []
offset  = 0
with open(path, 'rb') as fh:
    for raw_line in fh:
        if offset >= limit:
            break
        if move_re.match(raw_line.lstrip()):
            offsets.append(offset)
        offset += len(raw_line)

if not offsets:
    print(limit)
    sys.exit(0)

idx = max(0, len(offsets) - rewind)
print(offsets[idx])
PYEOF
  )"
  echo "Rewind      : 100 motion lines → byte $SLICE_BYTE (was $RAW_SLICE_BYTE)"
fi

# ── Extract header block from original file ──────────────────────────────────
# Strategy (in order of preference):
#   1. OrcaSlicer: content up to and including '; HEADER_BLOCK_END'
#   2. Fallback: first 50 lines
HEADER_END_BYTE="$(python3 - "$FULL_PATH" <<'PYEOF'
import sys

path = sys.argv[1]
sentinels = [b'; HEADER_BLOCK_END', b';HEADER_BLOCK_END']
offset = 0
with open(path, 'rb') as fh:
    for raw_line in fh:
        offset += len(raw_line)
        stripped = raw_line.strip()
        if stripped in sentinels:
            print(offset)
            sys.exit(0)

# Fallback: first 50 lines
print(-1)
PYEOF
)"

# ── Find last known Z before slice point (if not supplied) ───────────────────
if [[ -z "$Z_TARGET" && -z "$SPEED" ]] || [[ -z "$Z_TARGET" ]]; then
  # Auto-detect last Z before the slice byte
  LAST_Z_FOUND="$(python3 - "$FULL_PATH" "$SLICE_BYTE" <<'PYEOF'
import sys, re

path      = sys.argv[1]
limit     = int(sys.argv[2])
z_re      = re.compile(r'^(?:G0|G1)\s[^;]*Z([\d]+\.?[\d]*)', re.IGNORECASE)

last_z    = None
offset    = 0
with open(path, 'rb') as fh:
    for raw_line in fh:
        if offset >= limit:
            break
        line = raw_line.decode('utf-8', errors='replace')
        m = z_re.match(line)
        if m:
            last_z = m.group(1)
        offset += len(raw_line)

print(last_z if last_z is not None else '')
PYEOF
)"
  # Only override Z_TARGET if it wasn't explicitly supplied
  if [[ -z "$Z_TARGET" && -n "$LAST_Z_FOUND" ]]; then
    Z_TARGET="$LAST_Z_FOUND"
  fi
fi

# ── Build output filename ────────────────────────────────────────────────────
BASENAME="$(basename "$CLEAN")"
if [[ -n "$OUTNAME" ]]; then
  # Sanitise: strip path separators
  OUTNAME="${OUTNAME//\//}"
  OUTNAME="${OUTNAME//\\/}"
  OUT_FILE="$GCODES/$OUTNAME"
else
  OUT_FILE="$GCODES/reCover_${BASENAME}"
fi

echo "Output file : $(basename "$OUT_FILE")"

# ── Back up original ─────────────────────────────────────────────────────────
BACKUP_PATH="${FULL_PATH}.backup"
cp -f "$FULL_PATH" "$BACKUP_PATH"
echo "Backup      : $(basename "$BACKUP_PATH") created"

# ── Write recovery file ───────────────────────────────────────────────────────
TMP_OUT="${OUT_FILE}.tmp.$$"

{
  # 1. Write the original header block
  if [[ -n "$HEADER_END_BYTE" && "$HEADER_END_BYTE" != "-1" && "$HEADER_END_BYTE" -gt 0 ]]; then
    head -c "$HEADER_END_BYTE" "$FULL_PATH"
  else
    # Fallback: first 50 lines
    head -n 50 "$FULL_PATH"
  fi

  # 2. Recovery preamble comment
  printf '\n; ── reCover preamble ──────────────────────────────────\n'
  printf '; Generated by re3D Machine Configurator print recovery\n'
  printf '; Original file : %s\n' "$BASENAME"
  printf '; Slice byte    : %d\n' "$SLICE_BYTE"
  if [[ -n "$Z_TARGET" ]]; then
    printf '; Resume Z      : %s mm\n' "$Z_TARGET"
  fi
  printf '; ────────────────────────────────────────────────────────\n'

  # 3. Recovery move sequence.
  #    SET_KINEMATIC_POSITION tells Klipper where the toolhead physically is
  #    without any movement, properly initialising the motion system.
  #    G92 Z only applies a coordinate offset and is not sufficient in Klipper.

  # Start heating early so temps rise during homing (saves time)
  if [[ -n "$BED_TEMP" ]]; then
    printf 'M140 S%s        ; start heating bed (no wait)\n' "$BED_TEMP"
  fi
  if [[ -n "$HOTEND_TEMP" ]]; then
    printf 'M104 S%s        ; start heating hotend (no wait)\n' "$HOTEND_TEMP"
  fi
  printf 'G90             ; absolute positioning\n'
  if [[ -n "$Z_TARGET" ]]; then
    # Set kinematic position so Klipper knows where Z is (no movement)
    printf 'SET_KINEMATIC_POSITION Z=%s  ; declare current Z position to Klipper\n' "$Z_TARGET"
    # Lift 2 mm above the resume point to clear the nozzle from the print
    printf 'G91             ; relative mode\n'
    printf 'G1 Z2 F300      ; lift 2 mm clear of print\n'
    printf 'G90             ; back to absolute mode\n'
  fi
  # Home X and Y (safe now that Z is lifted)
  printf 'G28 X Y         ; home X and Y axes\n'
  # Wait for temperatures before purging
  if [[ -n "$BED_TEMP" ]]; then
    printf 'M190 S%s        ; wait for bed temperature\n' "$BED_TEMP"
  fi
  if [[ -n "$HOTEND_TEMP" ]]; then
    printf 'M109 S%s        ; wait for hotend temperature\n' "$HOTEND_TEMP"
  fi
  # Prime/purge nozzle before resuming
  printf 'G92 E0          ; reset extruder position\n'
  printf 'M83             ; extruder relative mode\n'
  printf 'G1 E50 F300     ; purge 50 mm of filament\n'
  printf 'M106 S255       ; cooling fan full on\n'
  if [[ -n "$SPEED" ]] && echo "$SPEED" | grep -qE '^[0-9]+(\.[0-9]+)?$'; then
    printf 'G1 F%s          ; restore last known speed\n' "$SPEED"
  fi
  printf 'G92 E0          ; reset extruder again before resuming\n'

  printf '; ── resume from original file ──────────────────────────\n\n'

  # 4. Tail of original file from slice byte onwards
  tail -c +$((SLICE_BYTE + 1)) "$FULL_PATH"

} > "$TMP_OUT"

# Verify non-empty
OUT_SIZE="$(stat -c%s "$TMP_OUT" 2>/dev/null || echo 0)"
if [[ "$OUT_SIZE" -le 0 ]]; then
  rm -f "$TMP_OUT"
  echo "ERROR: Recovery file creation failed (empty output)"
  exit 0
fi

mv -f "$TMP_OUT" "$OUT_FILE"
sync

echo "Done"
echo "Recovery file : $(basename "$OUT_FILE") ($(stat -c%s "$OUT_FILE") bytes)"
echo ""
echo "You can now start this file from Mainsail or Fluidd."
