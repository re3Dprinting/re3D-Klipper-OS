#!/usr/bin/env python3
"""re3D Print Recovery State Monitor

Connects to Moonraker via WebSocket (same as the browser tab) and receives
printer state updates in real-time.  In-memory state is always current.
State is written to disk:
  - immediately when the Z height changes (new layer)
  - every FLUSH_INTERVAL seconds as a heartbeat while printing
  - immediately when a print stops (accurate last-known position)

On a clean print completion (progress >= 99.9 %) the state file is removed.

No third-party dependencies — uses only Python 3 stdlib.
"""

import base64
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime

# ── Configuration ─────────────────────────────────────────────────────────────
WS_HOST         = "localhost"
WS_PORT         = 7125
WS_PATH         = "/websocket"
STATE_FILE      = "/home/pi/printer_data/recover_state.json"
FLUSH_INTERVAL  = 30      # seconds — heartbeat disk write while printing
RECONNECT_WAIT  = 5       # seconds — pause before reconnecting after a drop
COMPLETE_THRESH = 0.999   # progress fraction treated as "finished"


# ── Minimal RFC 6455 WebSocket client (stdlib only) ───────────────────────────

def _ws_handshake(sock):
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {WS_PATH} HTTP/1.1\r\n"
        f"Host: {WS_HOST}:{WS_PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(request.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Connection closed during handshake")
        buf += chunk
    status_line = buf.split(b"\r\n")[0]
    if b"101" not in status_line:
        raise ConnectionError(f"Upgrade rejected: {status_line.decode(errors='replace')}")


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("WebSocket connection closed")
        data += chunk
    return data


def _recv_frame(sock):
    """Return (opcode, payload_bytes) for one WebSocket frame."""
    header = _recv_exact(sock, 2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask_key = _recv_exact(sock, 4) if masked else b""
    payload  = _recv_exact(sock, length)
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _send_frame(sock, text):
    """Send a masked text frame (clients must mask per RFC 6455)."""
    payload  = text.encode("utf-8")
    mask_key = os.urandom(4)
    masked   = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    length   = len(payload)
    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0xFE]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 0xFF]) + struct.pack(">Q", length)
    sock.sendall(header + mask_key + masked)


def _send_ping(sock):
    sock.sendall(bytes([0x89, 0x80]) + os.urandom(4))  # masked empty ping


# ── State file helpers ─────────────────────────────────────────────────────────

def write_state(state):
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        _log(f"WARNING: Failed to write state: {e}")


def clear_state():
    try:
        os.remove(STATE_FILE)
        _log("State file cleared (print completed cleanly).")
    except FileNotFoundError:
        pass


def _log(msg):
    print(f"[recovery-monitor] {msg}", flush=True)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    _log("Starting.")
    while True:
        try:
            _run_session()
        except Exception as e:
            _log(f"Session error: {e} — reconnecting in {RECONNECT_WAIT}s")
        time.sleep(RECONNECT_WAIT)


def _run_session():
    sock = socket.create_connection((WS_HOST, WS_PORT), timeout=10)
    sock.settimeout(5.0)   # timeout on recv so we can do periodic work
    _ws_handshake(sock)
    _log(f"WebSocket connected to {WS_HOST}:{WS_PORT}")

    # Subscribe to the same objects as the browser tab
    _send_frame(sock, json.dumps({
        "jsonrpc": "2.0",
        "method":  "printer.objects.subscribe",
        "params":  {"objects": {
            "virtual_sdcard": None,
            "gcode_move":     ["speed", "position"],
        }},
        "id": 1,
    }))

    # In-memory printer state (merged incrementally, same as browser)
    vsd   = {}
    gmove = {}
    last_flush   = 0.0
    last_z       = None
    was_active   = False
    ping_due     = time.monotonic() + 30

    def _apply(new_vsd, new_gmove):
        nonlocal last_z, was_active, last_flush

        if new_vsd:
            vsd.update(new_vsd)
        if new_gmove:
            gmove.update(new_gmove)

        is_active     = vsd.get("is_active", False)
        file_path     = vsd.get("file_path", "")
        file_position = vsd.get("file_position")
        file_size     = vsd.get("file_size")
        progress      = vsd.get("progress") or 0

        position = gmove.get("position") or []
        cur_x    = round(position[0], 3) if len(position) > 0 else None
        cur_y    = round(position[1], 3) if len(position) > 1 else None
        cur_z    = round(position[2], 3) if len(position) > 2 else None
        speed    = int(gmove["speed"]) if gmove.get("speed") is not None else None

        if is_active and file_path and file_position is not None:
            if progress >= COMPLETE_THRESH:
                clear_state()
                was_active = False
                last_z     = None
                return

            basename = os.path.basename(file_path)
            if not was_active:
                _log(f"Tracking: {basename}")
                was_active = True

            now       = time.monotonic()
            z_changed = cur_z is not None and cur_z != last_z
            heartbeat = (now - last_flush) >= FLUSH_INTERVAL

            if z_changed or heartbeat:
                write_state({
                    "file":          basename,
                    "file_path":     file_path,
                    "file_position": file_position,
                    "file_size":     file_size,
                    "last_x":        cur_x,
                    "last_y":        cur_y,
                    "last_z":        cur_z,
                    "last_speed":    speed,
                    "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                last_flush = now
                if z_changed:
                    last_z = cur_z

        else:
            if was_active:
                # Print just stopped — flush final accurate position immediately
                _log("Print stopped — flushing final position to disk.")
                if file_path and file_position is not None:
                    write_state({
                        "file":          os.path.basename(file_path),
                        "file_path":     file_path,
                        "file_position": file_position,
                        "file_size":     file_size,
                        "last_x":        cur_x,
                        "last_y":        cur_y,
                        "last_z":        cur_z,
                        "last_speed":    speed,
                        "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                was_active = False
                last_z     = None

    # ── Receive loop ───────────────────────────────────────────────────────────
    while True:
        now = time.monotonic()
        if now >= ping_due:
            _send_ping(sock)
            ping_due = now + 30

        try:
            opcode, payload = _recv_frame(sock)
        except socket.timeout:
            continue   # nothing received — loop for ping check

        if opcode == 0x8:   # close frame
            _log("Server sent close frame.")
            break
        if opcode == 0xA:   # pong — ignore
            continue
        if opcode not in (0x1, 0x2):
            continue

        try:
            msg = json.loads(payload)
        except Exception:
            continue

        # Initial subscription response (id=1) — full current state
        if msg.get("id") == 1 and isinstance(msg.get("result"), dict):
            s = msg["result"].get("status", {})
            _apply(s.get("virtual_sdcard"), s.get("gcode_move"))
            continue

        # Incremental push updates
        if msg.get("method") == "notify_status_update":
            params = msg.get("params", [{}])
            p = params[0] if params else {}
            _apply(p.get("virtual_sdcard"), p.get("gcode_move"))


if __name__ == "__main__":
    main()
