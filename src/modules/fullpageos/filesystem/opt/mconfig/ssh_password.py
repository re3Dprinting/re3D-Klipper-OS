#!/usr/bin/env python3
"""Restricted root helper for changing only the pi account password."""

import ctypes
import hmac
import json
import logging
import os
from pathlib import Path
import socketserver
import subprocess


SOCKET = "/run/re3d-ssh-password/password.sock"


def shadow_record():
    result = subprocess.run(["getent", "shadow", "pi"], check=True, capture_output=True,
                            text=True, timeout=5)
    fields = result.stdout.strip().split(":")
    if len(fields) != 9 or fields[0] != "pi":
        raise ValueError("Cannot read the pi account password")
    return fields


def matches(password, hashed):
    library = ctypes.CDLL("libcrypt.so.1")
    library.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    library.crypt.restype = ctypes.c_char_p
    result = library.crypt(password.encode("utf-8"), hashed.encode("ascii"))
    if not result or result.startswith(b"*"):
        raise ValueError("The system password hash cannot be verified")
    return hmac.compare_digest(result, hashed.encode("ascii"))


def password_ready():
    import time

    fields = shadow_record()
    hashed = fields[1]
    if not hashed or hashed.startswith(("!", "*")):
        return False
    today = int(time.time() // 86400)
    if fields[7] and int(fields[7]) >= 0 and int(fields[7]) <= today:
        return False
    if fields[2] == "0":
        return False
    if (fields[2] and fields[4] and int(fields[4]) >= 0
            and int(fields[2]) + int(fields[4]) <= today):
        return False
    return not matches("raspberry", hashed)


def change_password(data):
    if not isinstance(data, dict) or set(data) != {"old", "new"}:
        return 400, {"error": "Expected the current and new pi passwords"}
    old, new = data["old"], data["new"]
    if any(not isinstance(value, str) or not value or len(value) > 1024
           or any(char in value for char in "\r\n\0") for value in (old, new)):
        return 400, {"error": "Invalid pi password"}
    if len(new) < 8 or new == "raspberry" or new == old:
        return 400, {"error": "Use a different, non-default pi password of at least 8 characters"}
    hashed = shadow_record()[1]
    if not hashed or hashed.startswith(("!", "*")) or not matches(old, hashed):
        return 403, {"error": "Current pi password is incorrect or the account is locked"}
    # Passwords go through stdin, never shell commands, process arguments or logs.
    result = subprocess.run(["chpasswd"], input=f"pi:{new}\n", text=True,
                            capture_output=True, timeout=15)
    if result.returncode:
        logging.error("System password update failed (exit %s)", result.returncode)
        return 500, {"error": "The system rejected the pi password change"}
    return 200, {"ok": True}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        try:
            line = self.rfile.readline(8193)
            if len(line) > 8192 or not line.endswith(b"\n"):
                status, body = 400, {"error": "Invalid password request size"}
            else:
                status, body = change_password(json.loads(line))
        except (ValueError, UnicodeError):
            status, body = 400, {"error": "Invalid password request"}
        except (OSError, subprocess.SubprocessError):
            logging.error("System password helper failed")
            status, body = 500, {"error": "Could not update the pi password"}
        self.wfile.write(json.dumps({"status": status, **body}).encode() + b"\n")


def main():
    Path(SOCKET).unlink(missing_ok=True)
    os.umask(0o117)
    with socketserver.UnixStreamServer(SOCKET, Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
