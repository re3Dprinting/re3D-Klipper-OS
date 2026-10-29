import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import parse_qs


def read_text(path):
    return Path(path).read_text().strip()


def run_command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"{args[0]} exited {result.returncode}")
    return result.stdout.strip()


def usage_status(percent):
    return "error" if percent >= 95 else "warning" if percent >= 85 else "ok"


def temperature():
    value = float(read_text("/sys/class/thermal/thermal_zone0/temp")) / 1000
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid CPU temperature")
    status = "error" if value >= 85 else "warning" if value >= 75 else "ok"
    return status, f"{value:.1f} C"


def cpu_load():
    loads = [float(value) for value in read_text("/proc/loadavg").split()[:3]]
    if len(loads) != 3 or any(not math.isfinite(value) or value < 0 for value in loads):
        raise ValueError("Invalid CPU load counters")
    cores = os.cpu_count()
    if not cores:
        raise ValueError("CPU count unavailable")
    status = "warning" if loads[0] >= cores else "ok"
    return status, f"{' / '.join(f'{value:.2f}' for value in loads)} (1 / 5 / 15 min; {cores} cores)"


def memory():
    values = {}
    for line in read_text("/proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0])
    total, available = values["MemTotal"], values["MemAvailable"]
    if not 0 <= available <= total or total <= 0:
        raise ValueError("Invalid memory counters")
    used = total - available
    percent = used / total * 100
    return usage_status(percent), f"{percent:.1f}% used ({used / 1024:.0f} / {total / 1024:.0f} MiB)"


def disk(path):
    usage = shutil.disk_usage(path)
    # Include reserved blocks: report space actually available to the printer user.
    percent = (usage.total - usage.free) / usage.total * 100
    return usage_status(percent), f"{percent:.1f}% used; {usage.free / 1024**3:.1f} GiB available"


def uptime():
    seconds = float(read_text("/proc/uptime").split()[0])
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid uptime")
    minutes = int(seconds) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return "ok", f"{days}d {hours}h {minutes}m"


def throttling():
    executable = shutil.which("vcgencmd")
    if not executable and Path("/opt/vc/bin/vcgencmd").is_file():
        executable = "/opt/vc/bin/vcgencmd"
    if not executable:
        raise ValueError("vcgencmd is not installed")
    output = run_command([executable, "get_throttled"])
    if not output.startswith("throttled=0x"):
        raise ValueError(f"Unexpected vcgencmd response: {output}")
    flags = int(output.split("=", 1)[1], 16)
    labels = ["Undervoltage", "Frequency capped", "Throttling", "Soft temperature limit"]
    current = [label for bit, label in enumerate(labels) if flags & (1 << bit)]
    previous = [label for bit, label in enumerate(labels) if flags & (1 << (bit + 16))]
    details = []
    if current:
        details.append("Now: " + ", ".join(current))
    if previous:
        details.append("Since boot: " + ", ".join(previous))
    if flags & ~0xF000F:
        details.append(f"Unrecognized flags: {flags:#x}")
    status = "error" if current else "warning" if flags else "ok"
    return status, "; ".join(details) or "No undervoltage or throttling detected since boot"


def service(name):
    output = run_command([
        "systemctl", "show", f"{name}.service",
        "--property=LoadState,ActiveState,SubState",
    ])
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    loaded = properties["LoadState"]
    active, sub = properties["ActiveState"], properties["SubState"]
    if loaded != "loaded":
        return "error", f"Unit {loaded}"
    if active == "active" and sub == "running":
        return "ok", "Active (running)"
    status = "warning" if active in ("activating", "reloading", "deactivating") else "error"
    return status, f"{active} ({sub})"


def collect_health(scope="all"):
    if scope not in ("all", "live"):
        raise ValueError("scope must be all or live")
    checks = [
        ("CPU temperature", temperature),
        ("CPU load", cpu_load),
        ("Memory", memory),
        ("System storage (/)", lambda: disk("/")),
        ("Printer storage", lambda: disk("/home/pi/printer_data")),
        ("Uptime", uptime),
        ("Power & throttling", throttling),
        ("Klipper service", lambda: service("klipper")),
        ("Moonraker service", lambda: service("moonraker")),
    ]
    if scope == "live":
        live_labels = {"CPU temperature", "CPU load", "Memory", "Uptime"}
        checks = [(label, check) for label, check in checks if label in live_labels]
    results = []
    for label, check in checks:
        try:
            status, detail = check()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            status, detail = "unknown", f"Unavailable: {error}"
        results.append({"label": label, "status": status, "detail": detail})
    return {"checks": results}


if __name__ == "__main__":
    scopes = parse_qs(os.environ.get("QUERY_STRING", ""), keep_blank_values=True).get("scope", ["all"])
    headers = "Content-Type: application/json\r\nCache-Control: no-store\r\n\r\n"
    if len(scopes) != 1 or scopes[0] not in ("all", "live"):
        headers = "Status: 400 Bad Request\r\n" + headers
        data = {"error": "scope must be all or live"}
    else:
        data = collect_health(scopes[0])
    sys.stdout.buffer.write((headers + json.dumps(data) + "\n").encode("utf-8"))
