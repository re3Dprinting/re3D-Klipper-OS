#!/usr/bin/python3
"""Track whether setup is pending or completed, without saving individual steps."""

import json
import os
from pathlib import Path
import sys
import tempfile


STATE = Path("/var/lib/re3d-setup/step")
FLASH_FLAG = Path("/etc/firstboot-splash")
STATES = ("machine", "openbox", "completed")


def normalize_state(step):
    # Older browser tabs may still send step names after an OS update.
    if step in ("welcome", "network", "display", "finish"):
        return "machine"
    if step not in STATES:
        raise ValueError("Invalid setup state.")
    return step


def read_step():
    try:
        step = STATE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    return normalize_state(step)


def save_step(step):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii",
                                         dir=STATE.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(step + "\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        temporary.replace(STATE)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def respond(status, payload):
    print(f"Status: {status}")
    print("Content-Type: application/json")
    print("Cache-Control: no-store")
    print()
    print(json.dumps(payload))


def main():
    try:
        method = os.environ.get("REQUEST_METHOD", "GET")
        if method not in ("GET", "POST"):
            respond("405 Method Not Allowed", {"error": "Use GET or POST."})
            return
        if method == "POST":
            if FLASH_FLAG.exists():
                respond("409 Conflict", {"error": "Finish firmware flashing and power-cycle first."})
                return
            length = int(os.environ.get("CONTENT_LENGTH", "0"))
            if not 0 < length <= 1024:
                raise ValueError("Invalid setup request length.")
            payload = json.loads(sys.stdin.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid setup state.")
            save_step(normalize_state(payload.get("step")))
        respond("200 OK", {"step": read_step(), "flashing": FLASH_FLAG.exists()})
    except (ValueError, UnicodeError) as error:
        print(f"Setup: {error}", file=sys.stderr)
        respond("400 Bad Request", {"error": str(error)})
    except OSError as error:
        print(f"Setup state error: {error}", file=sys.stderr)
        respond("500 Internal Server Error", {"error": "Unable to read or save setup status."})


if __name__ == "__main__":
    main()
