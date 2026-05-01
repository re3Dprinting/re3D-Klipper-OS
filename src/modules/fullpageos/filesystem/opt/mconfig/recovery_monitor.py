#!/usr/bin/env python3
"""re3D Print Recovery State Monitor

Polls Moonraker every 30 s while a print is active and writes
  /home/pi/printer_data/recover_state.json
so the print position can be recovered after a power loss or crash,
even if the browser was never opened.

On a clean print completion (progress >= 99.9 %) the state file is
removed so stale recovery prompts are not shown.
"""

import json
import os
import sys
import time
from datetime import datetime
import urllib.request
import urllib.error

MOONRAKER_URL   = "http://localhost:7125"
STATE_FILE      = "/home/pi/printer_data/recover_state.json"
POLL_ACTIVE     = 30    # seconds between writes while a print is running
POLL_IDLE       = 10    # seconds between checks while idle
COMPLETE_THRESH = 0.999 # progress fraction treated as "finished"


def query_moonraker():
    url = (
        f"{MOONRAKER_URL}/printer/objects/query"
        f"?virtual_sdcard=file_position,file_size,progress,is_active,file_path"
        f"&gcode_move=speed,position"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("result", {}).get("status", {})
    except Exception:
        return None


def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def write_state(state):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[recovery-monitor] Failed to write state: {e}", file=sys.stderr, flush=True)


def clear_state():
    try:
        os.remove(STATE_FILE)
        print("[recovery-monitor] State file cleared (print completed).", flush=True)
    except FileNotFoundError:
        pass


def main():
    print("[recovery-monitor] Starting.", flush=True)
    was_active = False

    while True:
        status = query_moonraker()
        if status is None:
            time.sleep(POLL_IDLE)
            continue

        vsd   = status.get("virtual_sdcard", {})
        gmove = status.get("gcode_move", {})

        is_active     = vsd.get("is_active", False)
        file_path     = vsd.get("file_path", "")
        file_position = vsd.get("file_position")
        file_size     = vsd.get("file_size")
        progress      = vsd.get("progress", 0)

        speed    = gmove.get("speed")
        position = gmove.get("position") or []
        last_x   = position[0] if len(position) >= 1 else None
        last_y   = position[1] if len(position) >= 2 else None
        last_z   = position[2] if len(position) >= 3 else None

        if is_active and file_path and file_position is not None:
            # Print finished cleanly — remove stale state
            if progress >= COMPLETE_THRESH:
                clear_state()
                was_active = False
                time.sleep(POLL_IDLE)
                continue

            basename = os.path.basename(file_path)
            state = {
                "file":          basename,
                "file_path":     file_path,
                "file_position": file_position,
                "file_size":     file_size,
                "last_x":        round(last_x, 3) if last_x is not None else None,
                "last_y":        round(last_y, 3) if last_y is not None else None,
                "last_z":        round(last_z, 3) if last_z is not None else None,
                "last_speed":    int(speed) if speed is not None else None,
                "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            write_state(state)
            if not was_active:
                print(f"[recovery-monitor] Tracking: {basename}", flush=True)
            was_active = True
            time.sleep(POLL_ACTIVE)

        else:
            if was_active:
                # Print just stopped — leave state on disk for recovery prompt
                print("[recovery-monitor] Print stopped; state preserved for recovery.", flush=True)
            was_active = False
            time.sleep(POLL_IDLE)


if __name__ == "__main__":
    main()
