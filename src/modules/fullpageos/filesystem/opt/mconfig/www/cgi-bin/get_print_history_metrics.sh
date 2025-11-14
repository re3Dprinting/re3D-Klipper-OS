#!/bin/sh
# CGI: aggregate Moonraker print history into metrics JSON.

echo "Content-Type: application/json"
echo ""

# Use python so we can easily call Moonraker + process JSON
python3 - << 'PY'
import json, sys, math, datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

BASE_URL = "http://localhost:7125/server/history/jobs?limit=500"

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
completed    = sum(1 for j in jobs if j.get("status") == "completed")
failed       = sum(1 for j in jobs if j.get("status") == "error")

# Durations are in seconds
total_print_time_s = sum(j.get("print_duration", 0) or 0 for j in jobs)
total_print_time_h = total_print_time_s / 3600.0
avg_print_time_h   = total_print_time_h / total_prints if total_prints > 0 else 0.0
success_rate       = (completed / total_prints) if total_prints > 0 else 0.0

# Last finished job time (epoch -> ISO8601)
last_end = None
for j in jobs:
    end_t = j.get("end_time")
    if end_t is None:
        continue
    if (last_end is None) or (end_t > last_end):
        last_end = end_t

if last_end is not None:
    last_dt = datetime.datetime.fromtimestamp(last_end)
    last_iso = last_dt.isoformat()
else:
    last_iso = None

# Longest single print
longest_job = None
longest_dur = 0.0
for j in jobs:
    d = j.get("print_duration") or 0
    if d > longest_dur:
        longest_dur = d
        longest_job = j

longest_hours = longest_dur / 3600.0 if longest_dur > 0 else 0.0
longest_name  = longest_job.get("job_name") if longest_job else None

# Group by period (month label)
from collections import defaultdict

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

summary = {
    "total_prints":          total_prints,
    "completed_prints":      completed,
    "failed_prints":         failed,
    "total_print_time_hours": round(total_print_time_h, 2),
    "avg_print_time_hours":   round(avg_print_time_h, 2),
    "success_rate":          success_rate,
    "last_print_finished":   last_iso,
    "longest_print_hours":   round(longest_hours, 2),
    "longest_print_name":    longest_name,
}

json.dump({"summary": summary, "by_period": by_period}, sys.stdout)
PY