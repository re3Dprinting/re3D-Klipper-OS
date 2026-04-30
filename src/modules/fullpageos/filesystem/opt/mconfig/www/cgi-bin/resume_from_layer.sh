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
#     including the first ';flag' comment, or the slicer HEADER_BLOCK if present).
#   - Injects a G28 Z / G92 E0 preamble, the last known Z lift, speed restore, and
#     then the remainder of the file from the slice point.
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

parse_kv() {
  local kv="$1" k v
  k="${kv%%=*}"
  v="${kv#*=}"
  v="$(printf '%s' "$v" | sed 's/+/ /g; s/%/\\x/g' | xargs -0 printf '%b' 2>/dev/null || printf '%s' "$v")"
  case "$k" in
    file)     FILE_PARAM="$v" ;;
    position) POSITION="$v"   ;;
    z)        Z_TARGET="$v"   ;;
    speed)    SPEED="$v"      ;;
    outname)  OUTNAME="$v"    ;;
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

# ── Extract header block from original file ──────────────────────────────────
# Strategy (in order of preference):
#   1. OrcaSlicer / Bambu: content up to and including '; HEADER_BLOCK_END'
#   2. Generic: content up to and including the first ';flag' sentinel
#   3. Fallback: first 50 lines
HEADER_END_BYTE="$(python3 - "$FULL_PATH" <<'PYEOF'
import sys

path = sys.argv[1]
sentinels = [b'; HEADER_BLOCK_END', b';HEADER_BLOCK_END', b';flag']
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
    dd if="$FULL_PATH" bs=1 count="$HEADER_END_BYTE" 2>/dev/null
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

  # 3. Safe move sequence: home Z, lift to resume height, reset extrusion
  printf 'G28 Z           ; home Z axis\n'
  printf 'G92 E0          ; reset extruder position\n'
  if [[ -n "$Z_TARGET" ]]; then
    printf 'G1 Z%s F600     ; lift to resume height\n' "$Z_TARGET"
  fi
  if [[ -n "$SPEED" ]] && echo "$SPEED" | grep -qE '^[0-9]+(\.[0-9]+)?$'; then
    printf 'G1 F%s          ; restore last known speed\n' "$SPEED"
  fi
  printf 'G92 E0          ; reset extruder again before resuming\n'
  printf '; ── resume from original file ──────────────────────────\n\n'

  # 4. Tail of original file from slice byte onwards
  dd if="$FULL_PATH" bs=1 skip="$SLICE_BYTE" 2>/dev/null

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
