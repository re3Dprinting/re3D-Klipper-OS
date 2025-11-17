#!/bin/sh
# CGI: aggregate Moonraker print history into metrics JSON.

echo "Content-Type: application/json"
echo ""

python3 - << 'PY'
import json, sys, datetime
from collections import defaultdict
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Moonraker endpoints
#   - totals: matches what Mainsail shows in its "Print Statistics" card
#   - list  : full job list for success ratio, breakdown, chart, etc.
# ---------------------------------------------------------------------------
LIST_URL   = "http://localhost:7125/server/history/list?start=0&limit=9999"
TOTALS_URL = "http://localhost:7125/server/history/totals"

def fetch_json(url):
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as resp:
            return json.load(resp)
    except (URLError, HTTPError, ValueError, TimeoutError):
        return None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Fetch data
# ---------------------------------------------------------------------------
list_data   = fetch_json(LIST_URL)   or {}
totals_data = fetch_json(TOTALS_URL) or {}

jobs = []
if isinstance(list_data, dict):
    jobs = list_data.get("result", {}).get("jobs", []) or []

job_totals = {}
if isinstance(totals_data, dict):
    job_totals = (totals_data.get("result") or {}).get("job_totals") or {}

# If we truly have nothing, bail out with empty metrics
if not jobs and not job_totals:
    json.dump({"summary": None, "by_period": []}, sys.stdout)
    sys.exit(0)

def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default

# ---------------------------------------------------------------------------
# Status counts from job list
# ---------------------------------------------------------------------------
total_jobs_from_list = len(jobs)

completed       = 0
failed          = 0
error_count     = 0
cancelled_count = 0
shutdown_count  = 0
other_count     = 0

for j in jobs:
    status = (j.get("status") or "").lower()
    if status == "completed":
        completed += 1
    elif status == "error":
        failed += 1
        error_count += 1
    elif status == "cancelled":
        failed += 1
        cancelled_count += 1
    elif status == "klippy_shutdown":
        shutdown_count += 1
        # treat shutdown as a kind of failure for percentage breakdown
        failed += 1
    else:
        other_count += 1

# ---------------------------------------------------------------------------
# Use Moonraker's job_totals for core values so we match Mainsail exactly
# ---------------------------------------------------------------------------
total_jobs = job_totals.get("total_jobs")
if total_jobs is None:
    total_jobs = total_jobs_from_list

# total_print_time is stored in seconds in job_totals
total_print_time_s = safe_float(
    job_totals.get("total_print_time", job_totals.get("total_time", 0.0)), 0.0
)
total_print_time_h = total_print_time_s / 3600.0 if total_print_time_s > 0 else 0.0

# Average print time: based on total_print_time / total_jobs
if total_jobs and total_jobs > 0:
    avg_print_time_h = total_print_time_h / float(total_jobs)
else:
    avg_print_time_h = 0.0

# Longest print duration (seconds) from job_totals if available
longest_print_s = safe_float(job_totals.get("longest_print", 0.0), 0.0)
longest_print_h = longest_print_s / 3600.0 if longest_print_s > 0 else 0.0

# Try to get a human-readable longest print name from the list
longest_job_name = None
if jobs:
    max_dur = -1.0
    name    = None
    for j in jobs:
        d = safe_float(j.get("print_duration", 0.0), 0.0)
        if d > max_dur:
            max_dur = d
            name = j.get("job_name") or j.get("filename")
    if max_dur > 0:
        longest_job_name = name

# Last finished time: prefer job_totals.last_job if present, else scan jobs
last_iso = None
last_end_epoch = None

last_job = job_totals.get("last_job")
if isinstance(last_job, dict):
    end_t = last_job.get("end_time")
    if end_t is not None:
        try:
            last_end_epoch = float(end_t)
        except (TypeError, ValueError):
            last_end_epoch = None

if last_end_epoch is None:
    for j in jobs:
        end_t = j.get("end_time")
        if end_t is None:
            continue
        try:
            end_f = float(end_t)
        except (TypeError, ValueError):
            continue
        if (last_end_epoch is None) or (end_f > last_end_epoch):
            last_end_epoch = end_f

if last_end_epoch is not None:
    last_dt = datetime.datetime.fromtimestamp(last_end_epoch)
    last_iso = last_dt.isoformat()

# Success rate: based on completed / total_jobs
if total_jobs and total_jobs > 0:
    success_rate = completed / float(total_jobs)
else:
    success_rate = 0.0

# Filament totals (Moonraker stores in mm)
total_filament_mm = safe_float(job_totals.get("total_filament_used", 0.0), 0.0)
total_filament_m  = total_filament_mm / 1000.0 if total_filament_mm > 0 else 0.0
if total_jobs and total_jobs > 0:
    avg_filament_m = total_filament_m / float(total_jobs)
else:
    avg_filament_m = 0.0

# Percentages for breakdown
def pct(count):
    return (count / float(total_jobs)) if (total_jobs and total_jobs > 0) else 0.0

status_breakdown = {
    "completed":        int(completed),
    "error":            int(error_count),
    "cancelled":        int(cancelled_count),
    "klippy_shutdown":  int(shutdown_count),
    "other":            int(other_count),
}

status_rates = {
    "completed":       pct(completed),
    "error":           pct(error_count),
    "cancelled":       pct(cancelled_count),
    "klippy_shutdown": pct(shutdown_count),
    "other":           pct(other_count),
}

# ---------------------------------------------------------------------------
# Group by period (month label) for chart
# ---------------------------------------------------------------------------
by_period_hours = defaultdict(float)

for j in jobs:
    start = j.get("start_time")
    if not start:
        continue
    try:
        dt = datetime.datetime.fromtimestamp(float(start))
    except (TypeError, ValueError, OSError, OverflowError):
        continue
    label = dt.strftime("%b %Y")  # e.g. "Nov 2025"
    hours = safe_float(j.get("print_duration", 0.0), 0.0) / 3600.0
    if hours > 0:
        by_period_hours[label] += hours

by_period = [
    {"label": label, "hours": round(hours, 2)}
    for label, hours in sorted(by_period_hours.items(), key=lambda kv: kv[0])
]

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
summary = {
    "total_prints":            int(total_jobs),
    "completed_prints":        int(completed),
    "failed_prints":           int(failed),
    "total_print_time_hours":  round(total_print_time_h, 2),
    "avg_print_time_hours":    round(avg_print_time_h, 2),
    "success_rate":            success_rate,
    "last_print_finished":     last_iso,
    "longest_print_hours":     round(longest_print_h, 2),
    "longest_print_name":      longest_job_name,
    "total_filament_m":        round(total_filament_m, 2),
    "avg_filament_m":          round(avg_filament_m, 2),
    "status_breakdown":        status_breakdown,
    "status_rates":            status_rates,
}

json.dump({"summary": summary, "by_period": by_period}, sys.stdout)
PY
