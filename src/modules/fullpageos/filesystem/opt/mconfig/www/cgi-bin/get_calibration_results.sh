#!/bin/sh
# CGI: expose calibration profiles as JSON for the report page.
# Reads calibration_data/globals.env and merges latest flow/PA from
# calibration_data/calibration_results.log for the current machine/extruder.

echo "Content-Type: application/json"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
GLOBALS_FILE="$DATA_DIR/globals.env"
RESULTS_FILE="$DATA_DIR/calibration_results.log"

python3 - "$GLOBALS_FILE" "$RESULTS_FILE" << 'PY'
import json, os, sys

def parse_env_file(path):
    data = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # strip surrounding quotes
                if len(val) >= 2 and val[0] in ("'", '"') and val[-1] == val[0]:
                    val = val[1:-1]
                data[key] = val
    except Exception:
        return {}
    return data

def to_float(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def unescape_x(val):
    if not val:
        return val
    try:
        # Turn "Gigabot\\x204" into "Gigabot 4"
        return val.encode("latin1").decode("unicode_escape")
    except Exception:
        return val

def parse_results(path, target_machine, target_extruder):
    """
    Read calibration_results.log and return latest flow + PA for the
    given machine + extruder (matching both).
    Format from your scripts:
      timestamp | type | mode | machine | extruder | hotend | bed | value | notes
    """
    latest_flow_val = None
    latest_flow_notes = ""
    latest_pa_val = None
    latest_pa_notes = ""

    if not os.path.isfile(path):
        return latest_flow_val, latest_flow_notes, latest_pa_val, latest_pa_notes

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 9:
                    continue
                ts, typ, mode, machine, extruder, hotend, bed, value, notes = parts[:9]

                # Normalize strings like "Gigabot\\x204"
                machine_u = unescape_x(machine)
                target_machine_u = unescape_x(target_machine)

                # Match this line to the active profile
                if target_machine_u and machine_u != target_machine_u:
                    continue
                if target_extruder and extruder != target_extruder:
                    continue

                if typ == "flow":
                    latest_flow_val = value
                    latest_flow_notes = notes
                elif typ == "pressure_advance":
                    latest_pa_val = value
                    latest_pa_notes = notes
    except Exception:
        pass

    return latest_flow_val, latest_flow_notes, latest_pa_val, latest_pa_notes


# ---- main ----
if len(sys.argv) < 2:
    json.dump({"profiles": []}, sys.stdout)
    sys.exit(0)

env_path = sys.argv[1]
results_path = sys.argv[2] if len(sys.argv) > 2 else None

env_data = parse_env_file(env_path)
if not env_data:
    json.dump({"profiles": []}, sys.stdout)
    sys.exit(0)

hotend   = env_data.get("HOTEND_TEMP")
bed      = env_data.get("BED_TEMP")
machine  = unescape_x(env_data.get("MACHINE"))
material = unescape_x(env_data.get("MATERIAL"))
extruder = env_data.get("EXTRUDER")
updated  = env_data.get("UPDATED_AT")

flow_val = None
flow_notes = ""
pa_val = None
pa_notes = ""

if results_path:
    flow_val, flow_notes, pa_val, pa_notes = parse_results(
        results_path, machine, extruder
    )

profile = {
    "machine":      machine,
    "material":     material,
    "extruder":     extruder,
    "hotend_temp":  to_float(hotend),
    "bed_temp":     to_float(bed),
    "flow":         to_float(flow_val),
    "flow_notes":   flow_notes,
    "pa_k":         to_float(pa_val),
    "pa_notes":     pa_notes,
    "updated_at":   updated,
}

data = {"profiles": [profile]}
json.dump(data, sys.stdout)
PY