#!/bin/sh
# CGI: build calibration report JSON from calibration_results.log + globals.env

echo "Content-Type: application/json"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WWW_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$WWW_ROOT/calibration_data"
RESULTS_FILE="$DATA_DIR/calibration_results.log"
GLOBALS_FILE="$DATA_DIR/globals.env"

export DATA_DIR
export RESULTS_FILE
export GLOBALS_FILE

python3 - << 'PYCODE'
import os, json, re

results_file = os.environ.get("RESULTS_FILE", "")
globals_file = os.environ.get("GLOBALS_FILE", "")

HEX_RE = re.compile(r"\\x([0-9A-Fa-f]{2})")

def unescape_hex(s: str) -> str:
    if not s:
        return ""
    return HEX_RE.sub(lambda m: chr(int(m.group(1), 16)), s)

def parse_globals(path):
    data = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            # strip surrounding quotes
            if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
                v = v[1:-1]
            # decode \xHH
            v = unescape_hex(v)
            data[k] = v
    return data

profiles = {}  # key -> profile dict

def get_profile_key(machine, material, extruder, hotend, bed):
    return "|".join([
        machine or "",
        material or "",
        extruder or "",
        "" if hotend is None else str(hotend),
        "" if bed is None else str(bed),
    ])

# ---- 1) Aggregate from calibration_results.log ------------------------------
if os.path.exists(results_file):
    with open(results_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # Expect: ts | type | mode | machine | material | extruder | hotend | bed | value | notes
            if len(parts) < 9:
                # older format; skip to avoid corrupting aggregation
                continue

            ts   = parts[0]
            typ  = parts[1]  # "flow" or "pressure_advance"
            mode = parts[2]
            machine  = unescape_hex(parts[3])
            material = unescape_hex(parts[4])
            extruder = parts[5]
            hotend   = parts[6]
            bed      = parts[7]
            value    = parts[8]
            notes    = parts[9] if len(parts) > 9 else ""

            try:
                hotend_f = float(hotend) if hotend else None
            except ValueError:
                hotend_f = None
            try:
                bed_f = float(bed) if bed else None
            except ValueError:
                bed_f = None
            try:
                value_f = float(value) if value else None
            except ValueError:
                value_f = None

            key = get_profile_key(machine, material, extruder, hotend_f, bed_f)
            prof = profiles.get(key)
            if not prof:
                prof = {
                    "machine": machine,
                    "material": material,
                    "extruder": extruder,
                    "hotend": hotend_f,
                    "bed": bed_f,
                    "flow": None,
                    "flow_updated_at": None,
                    "flow_mode": None,
                    "flow_notes": None,
                    "pressure_advance": None,
                    "pressure_advance_updated_at": None,
                    "pressure_advance_mode": None,
                    "pressure_advance_notes": None,
                }

            # Keep latest per type for THIS profile
            if typ == "flow":
                existing_ts = prof.get("flow_updated_at") or ""
                if not existing_ts or existing_ts <= ts:
                    prof["flow"] = value_f
                    prof["flow_updated_at"] = ts
                    prof["flow_mode"] = mode
                    prof["flow_notes"] = notes
            elif typ == "pressure_advance":
                existing_ts = prof.get("pressure_advance_updated_at") or ""
                if not existing_ts or existing_ts <= ts:
                    prof["pressure_advance"] = value_f
                    prof["pressure_advance_updated_at"] = ts
                    prof["pressure_advance_mode"] = mode
                    prof["pressure_advance_notes"] = notes

            profiles[key] = prof

# ---- 2) Inject current active globals as a profile (even if no results) ----
g = parse_globals(globals_file)
machine  = g.get("MACHINE", "")
material = g.get("MATERIAL", "")
extruder = g.get("EXTRUDER", "")
ht       = g.get("HOTEND_TEMP", "")
bt       = g.get("BED_TEMP", "")

try:
    ht_f = float(ht) if ht else None
except ValueError:
    ht_f = None
try:
    bt_f = float(bt) if bt else None
except ValueError:
    bt_f = None

if machine or material or extruder or ht or bt:
    key = get_profile_key(machine, material, extruder, ht_f, bt_f)
    if key not in profiles:
        profiles[key] = {
            "machine": machine,
            "material": material,
            "extruder": extruder,
            "hotend": ht_f,
            "bed": bt_f,
            "flow": None,
            "flow_updated_at": None,
            "flow_mode": None,
            "flow_notes": None,
            "pressure_advance": None,
            "pressure_advance_updated_at": None,
            "pressure_advance_mode": None,
            "pressure_advance_notes": None,
        }

# ---- 3) Build response ------------------------------------------------------
profile_list = []
for idx, (key, prof) in enumerate(sorted(
        profiles.items(),
        key=lambda kv: (
            kv[1].get("machine") or "",
            kv[1].get("material") or "",
            kv[1].get("extruder") or ""
        ))):
    p = dict(prof)
    p["id"] = str(idx + 1)
    profile_list.append(p)

response = {
    "profiles": profile_list
}

print(json.dumps(response))
PYCODE