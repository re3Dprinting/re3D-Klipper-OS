#!/usr/bin/env python3
"""Loopback-only authentication service for the two nginx web interfaces."""

import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time


COOKIE = "re3d_session"
SESSION_SECONDS = 8 * 60 * 60
MAX_ENTRIES = 4096
ROOT = Path(__file__).resolve().parent


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                          n=32768, r=8, p=1, maxmem=64 * 1024 * 1024).hex()


class AuthState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}
        self.record = None
        if self.path.exists():
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if record is not None:
                if (not isinstance(record, dict) or set(record) != {"salt", "hash"}
                        or not isinstance(record["salt"], str)
                        or not isinstance(record["hash"], str)
                        or len(bytes.fromhex(record["salt"])) != 32
                        or len(bytes.fromhex(record["hash"])) != 64):
                    raise ValueError("Invalid authentication state; refusing to disable protection")
            self.record = record

    def save(self, record):
        # Replace atomically on the same filesystem; never truncate the live credential.
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".auth-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.record = record
        self.sessions.clear()

    def verify(self, password):
        return self.record is not None and hmac.compare_digest(
            password_hash(password, self.record["salt"]), self.record["hash"])

    def prune(self):
        now = time.monotonic()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        self.attempts = {key: value for key, value in self.attempts.items() if value[1] > now}

    def attempt(self, address):
        self.prune()
        count, expiry = self.attempts.get(address, (0, time.monotonic() + 60))
        if count >= 5 or (address not in self.attempts and len(self.attempts) >= MAX_ENTRIES):
            return False
        self.attempts[address] = (count + 1, expiry)
        return True

    def session(self):
        self.prune()
        if len(self.sessions) >= MAX_ENTRIES:
            del self.sessions[next(iter(self.sessions))]
        token = secrets.token_urlsafe(32)
        self.sessions[token] = time.monotonic() + SESSION_SECONDS
        return token


class AuthHandler(BaseHTTPRequestHandler):
    server_version = "re3D"

    def log_message(self, fmt, *args):
        # Do not log request bodies, passwords, cookies or query strings.
        logging.info("Authentication request: %s %s", self.command, self.path.split("?")[0])

    @property
    def state(self):
        return self.server.state

    def reply(self, status, data, content_type="application/json", cookie=None):
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        if cookie is not None:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie",
                             f"{COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Strict"
                             f"{secure}" + ("; Max-Age=0" if not cookie else ""))
        self.end_headers()
        self.wfile.write(body)

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return cookie[COOKIE].value if COOKIE in cookie else ""

    def do_GET(self):
        with self.state.lock:
            if self.path == "/auth/check":
                self.state.prune()
                address = self.headers.get("X-Real-IP", "")
                local = address in ("127.0.0.1", "::1")
                allowed = self.state.record is None or local or self.token() in self.state.sessions
                self.reply(204 if allowed else 401, b"")
            elif self.path == "/auth/status":
                self.reply(200, {"enabled": self.state.record is not None})
            elif self.path in ("/auth/login", "/auth/settings"):
                self.reply(200, (ROOT / "auth.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self.reply(404, {"error": "Not found"})

    def do_POST(self):
        origin = f'{self.headers.get("X-Forwarded-Proto")}://{self.headers.get("Host")}'
        if self.headers.get("Origin") != origin:
            self.reply(403, {"error": "Same-origin requests are required"})
            return
        if self.headers.get("Content-Type") != "application/json":
            self.reply(415, {"error": "Expected application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            for key in ("password", "current", "new"):
                if key in data and (not isinstance(data[key], str) or len(data[key]) > 1024):
                    raise ValueError("Passwords must be strings of at most 1024 characters")
        except (ValueError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})
            return
        try:
            with self.state.lock:
                self.post(data)
        except OSError:
            logging.exception("Could not persist authentication state")
            self.reply(500, {"error": "Could not save password settings; protection was not changed"})

    def post(self, data):
        if self.path == "/auth/logout":
            self.state.sessions.pop(self.token(), None)
            self.reply(200, {"ok": True}, cookie="")
            return
        if self.path not in ("/auth/login", "/auth/password"):
            self.reply(404, {"error": "Not found"})
            return
        address = self.headers.get("X-Real-IP", "")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            self.reply(400, {"error": "Missing client address"})
            return
        if not self.state.attempt(address):
            self.reply(429, {"error": "Too many password attempts. Wait 60 seconds and try again."})
            return
        if self.path == "/auth/login":
            if self.state.record is None:
                self.reply(200, {"ok": True})
            elif not self.state.verify(data.get("password", "")):
                self.reply(403, {"error": "Incorrect password"})
            else:
                self.reply(200, {"ok": True}, cookie=self.state.session())
            return
        if self.state.record is not None and not self.state.verify(data.get("current", "")):
            self.reply(403, {"error": "Current password is incorrect"})
            return
        if data.get("disable") is True:
            self.state.save(None)
            self.reply(200, {"ok": True, "enabled": False}, cookie="")
            return
        password = data.get("new", "")
        if len(password) < 12:
            self.reply(400, {"error": "Use a password of at least 12 characters"})
            return
        salt = secrets.token_hex(32)
        self.state.save({"salt": salt, "hash": password_hash(password, salt)})
        self.reply(200, {"ok": True, "enabled": True}, cookie=self.state.session())


def serve():
    logging.basicConfig(level=logging.INFO)
    state = AuthState("/var/lib/re3d-auth/password.json")
    server = ThreadingHTTPServer(("127.0.0.1", 8090), AuthHandler)
    server.state = state
    server.serve_forever()


if __name__ == "__main__":
    serve()
