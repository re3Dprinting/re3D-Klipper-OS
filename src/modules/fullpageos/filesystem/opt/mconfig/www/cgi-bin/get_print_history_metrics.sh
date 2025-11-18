#!/bin/sh
# CGI: aggregate Moonraker print history into metrics JSON.

echo "Content-Type: application/json"
echo ""

# Use python so we can easily call Moonraker + process JSON
python3 - << 'PY'
import json, sys, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from collections import defaultdict

# Moonraker history endpoint (list-style)
BASE_URL = "http://localhost:7125/server/history/list?limit=500"

def fetch_jobs():
    try:
        req = Request(BASE_URL, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        return data.get("result", {}).get("jobs", [])
    except (URLError, HTTPError, ValueError, TimeoutError):
        return []

jobs = fetch_jobs()

if not jobs:
    json.dump({"summary": None, "by_period": []}, sys.stdout)
    sys.exit(0)

total_prints = len(jobs)

# --- Status counts + time (treat klippy_shutdown as cancelled) -------------
status_counts = {
    "completed": 0,
    "error": 0,
    "cancelled": 0,  # includes klippy_shutdown
    "other": 0,
}

status_time_s = {
    "completed": 0.0,
    "error": 0.0,
    "cancelled": 0.0,
    "other": 0.0,
}

total_filament_mm = 0.0
longest_dur = 0.0
longest_job = None
total_print_time_s = 0.0  # sum of print_duration across all jobs

for j in jobs:
    status = (j.get("status") or "").lower()
    dur = float(j.get("print_duration") or 0.0)
    total_print_time_s += dur

    if status == "completed":
        status_counts["completed"] += 1
        status_time_s["completed"] += dur
    elif status == "error":
        status_counts["error"] += 1
        status_time_s["error"] += dur
    elif status in ("cancelled", "klippy_shutdown"):
        status_counts["cancelled"] += 1
        status_time_s["cancelled"] += dur
    else:
        status_counts["other"] += 1
        status_time_s["other"] += dur

    # longest print by print_duration
    if dur > longest_dur:
        longest_dur = dur
        longest_job = j

    # filament_used is mm of filament
    total_filament_mm += float(j.get("filament_used") or 0.0)

completed = status_counts["completed"]
failed    = status_counts["error"]
cancelled = status_counts["cancelled"]

total_print_time_h = total_print_time_s / 3600.0
avg_print_time_h   = (total_print_time_h / total_prints) if total_prints > 0 else 0.0
success_rate       = (completed / total_prints) if total_prints > 0 else 0.0  # kept for reference, not displayed

# Filament in meters
total_filament_m = total_filament_mm / 1000.0 if total_filament_mm > 0 else None
avg_filament_m   = (total_filament_m / total_prints) if (total_filament_m is not None and total_prints > 0) else None

# Last finished job time (epoch -> ISO8601)
last_end = None
for j in jobs:
    end_t = j.get("end_time")
    if end_t is None:
        continue
    if (last_end is None) or (end_t > last_end):
        last_end = end_t

if last_end is not None:
    last_dt  = datetime.datetime.fromtimestamp(last_end)
    last_iso = last_dt.isoformat()
else:
    last_iso = None

# Longest single print
longest_hours = longest_dur / 3600.0 if longest_dur > 0 else 0.0
longest_name  = longest_job.get("job_name") if longest_job else None

# Group by period (month label)
by_period_hours = defaultdict(float)
for j in jobs:
    start = j.get("start_time")
    if not start:
        continue
    dt = datetime.datetime.fromtimestamp(start)
    label = dt.strftime("%b %Y")  # e.g. "Nov 2025"
    hours = (j.get("print_duration") or 0) / 3600.0
    by_period_hours[label] += hours

by_period = [
    {"label": label, "hours": round(hours, 2)}
    for label, hours in sorted(by_period_hours.items(), key=lambda kv: kv[0])
]

# Time-based breakdown per status
status_time_hours = {
    k: round(v / 3600.0, 2) for k, v in status_time_s.items()
}
total_time_for_share = sum(status_time_s.values())
if total_time_for_share > 0:
    status_time_rates = {k: v / total_time_for_share for k, v in status_time_s.items()}
else:
    status_time_rates = {k: 0.0 for k in status_time_s}

summary = {
    "total_prints":           total_prints,
    "completed_prints":       completed,
    "failed_prints":          failed,
    "cancelled_prints":       cancelled,
    "total_print_time_hours": round(total_print_time_h, 2),
    "avg_print_time_hours":   round(avg_print_time_h, 2),
    "success_rate":           success_rate,   # time-based ratios used in UI instead
    "last_print_finished":    last_iso,
    "longest_print_hours":    round(longest_hours, 2),
    "longest_print_name":     longest_name,
    "total_filament_m":       round(total_filament_m, 2) if total_filament_m is not None else None,
    "avg_filament_m":         round(avg_filament_m, 2) if avg_filament_m is not None else None,
    "status_breakdown":       status_counts,      # counts
    "status_time_hours":      status_time_hours,  # hours per status
    "status_time_rates":      status_time_rates,  # ratios per status (by time, not count)
}

json.dump({"summary": summary, "by_period": by_period}, sys.stdout)
PY
