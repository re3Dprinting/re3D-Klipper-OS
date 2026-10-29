#!/usr/bin/env python3
"""Loopback-only authentication service for the two nginx web interfaces."""

import hashlib
import hmac
import http.client
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
from urllib.parse import urlsplit


COOKIE = "re3d_session"
PIN_COOKIE = "re3d_kiosk"
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 5 * 60
APPLIED_MODE = Path("/run/re3d-security/mode")
MAX_ENTRIES = 4096
ROOT = Path(__file__).resolve().parent


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                          n=32768, r=8, p=1, maxmem=64 * 1024 * 1024).hex()


def credential(password):
    salt = secrets.token_hex(32)
    return {"salt": salt, "hash": password_hash(password, salt)}


def validate_credential(record):
    if (not isinstance(record, dict) or set(record) != {"salt", "hash"}
            or not isinstance(record["salt"], str) or not isinstance(record["hash"], str)
            or len(bytes.fromhex(record["salt"])) != 32
            or len(bytes.fromhex(record["hash"])) != 64):
        raise ValueError("Invalid authentication state; refusing to disable protection")


def lockdown_readiness():
    connection = http.client.HTTPConnection("127.0.0.1", 8085, timeout=5)
    try:
        connection.request("GET", "/cgi-bin/security_check.sh")
        response = connection.getresponse()
        data = json.loads(response.read(8192))
        if response.status != 200 or not isinstance(data, dict) or type(data.get("ready")) is not bool:
            raise ValueError("Invalid SSH prerequisite check response")
        return data
    finally:
        connection.close()


class AuthState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.sessions = {}
        self.pin_sessions = {}
        self.attempts = {}
        self.record = None
        if self.path.exists():
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if record is not None:
                if not isinstance(record, dict):
                    raise ValueError("Invalid authentication state")
                validate_credential({key: record[key] for key in ("salt", "hash") if key in record})
                if set(record) != {"salt", "hash"}:
                    if (set(record) != {"salt", "hash", "mode", "pin"}
                            or record["mode"] not in ("basic", "lockdown")):
                        raise ValueError("Invalid security mode")
                    if record["pin"] is not None:
                        validate_credential(record["pin"])
                    if record["mode"] == "lockdown" and record["pin"] is None:
                        raise ValueError("Lockdown requires a touchscreen PIN")
            self.record = record

    @property
    def mode(self):
        return self.record.get("mode", "basic") if self.record else "off"

    @property
    def has_pin(self):
        return self.record is not None and self.record.get("pin") is not None

    @property
    def applied_mode(self):
        return APPLIED_MODE.read_text().strip() if APPLIED_MODE.exists() else "off"

    @property
    def reboot_required(self):
        return (self.applied_mode == "lockdown") != (self.mode == "lockdown")

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
        self.pin_sessions.clear()

    def verify(self, password):
        return self.record is not None and hmac.compare_digest(
            password_hash(password, self.record["salt"]), self.record["hash"])

    def verify_pin(self, pin):
        return self.has_pin and hmac.compare_digest(
            password_hash(pin, self.record["pin"]["salt"]), self.record["pin"]["hash"])

    def prune(self):
        now = time.monotonic()
        expired = any(expiry <= now for expiry in (*self.sessions.values(), *self.pin_sessions.values()))
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        self.pin_sessions = {key: expiry for key, expiry in self.pin_sessions.items() if expiry > now}
        self.attempts = {key: value for key, value in self.attempts.items() if value[1] > now}
        if expired:
            self.revoke_connections()

    def revoke_connections(self):
        # A root-owned systemd path unit closes existing nginx websockets, not just future requests.
        self.path.with_name("revoke").write_text(str(time.monotonic()), encoding="ascii")

    def attempt(self, address):
        self.prune()
        count, expiry = self.attempts.get(address, (0, time.monotonic() + 60))
        if count >= 5 or (address not in self.attempts and len(self.attempts) >= MAX_ENTRIES):
            return False
        self.attempts[address] = (count + 1, expiry)
        return True

    def session(self, kiosk=False):
        self.prune()
        sessions = self.pin_sessions if kiosk else self.sessions
        if len(sessions) >= MAX_ENTRIES:
            del sessions[next(iter(sessions))]
        token = secrets.token_urlsafe(32)
        sessions[token] = time.monotonic() + (IDLE_SECONDS if kiosk else SESSION_SECONDS)
        return token


class AuthHandler(BaseHTTPRequestHandler):
    server_version = "re3D"

    def log_message(self, fmt, *args):
        # Do not log request bodies, passwords, cookies or query strings.
        logging.info("Authentication request: %s %s", self.command, self.path.split("?")[0])

    @property
    def state(self):
        return self.server.state

    def reply(self, status, data, content_type="application/json", cookie=None, cookie_name=COOKIE):
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
                         "frame-src 'self' http://localhost:8081; "
                         "base-uri 'none'; form-action 'self'")
        if cookie is not None:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie",
                             f"{cookie_name}={cookie}; Path=/; HttpOnly; SameSite=Strict"
                             f"{secure}" + ("; Max-Age=0" if not cookie else ""))
        self.end_headers()
        self.wfile.write(body)

    def token(self, name=COOKIE):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return cookie[name].value if name in cookie else ""

    @property
    def local(self):
        return self.headers.get("X-Real-IP", "") in ("127.0.0.1", "::1")

    def do_GET(self):
        path = urlsplit(self.path).path
        with self.state.lock:
            if path == "/auth/check":
                self.state.prune()
                if self.local and self.state.mode == "lockdown":
                    allowed = self.token(PIN_COOKIE) in self.state.pin_sessions
                else:
                    allowed = self.state.record is None or self.local or self.token() in self.state.sessions
                self.reply(204 if allowed else 401, b"")
            elif path == "/auth/status":
                self.reply(200, {"enabled": self.state.record is not None, "mode": self.state.mode,
                                "local": self.local, "has_pin": self.state.has_pin,
                                "applied_mode": self.state.applied_mode,
                                "reboot_required": self.state.reboot_required})
            elif path == "/auth/kiosk-status" and self.local:
                self.state.prune()
                self.reply(200, {"unlocked": self.token(PIN_COOKIE) in self.state.pin_sessions,
                                "mode": self.state.mode, "idle_seconds": IDLE_SECONDS})
            elif path == "/auth/kiosk" and self.local:
                self.reply(200, (ROOT / "kiosk.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/auth/kiosk-client.js":
                body = ((ROOT / "kiosk-client.js").read_bytes()
                        if self.local and self.state.mode == "lockdown" else b"")
                self.reply(200, body, "application/javascript")
            elif path == "/auth/security.css":
                self.reply(200, (ROOT / "security.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/auth/logo.png":
                self.reply(200, (ROOT / "www/img/re3d-logo.png").read_bytes(), "image/png")
            elif path in ("/auth/login", "/auth/settings"):
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
            for key in ("password", "current", "new", "pin"):
                if key in data and (not isinstance(data[key], str) or len(data[key]) > 1024):
                    raise ValueError("Passwords must be strings of at most 1024 characters")
        except (ValueError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})
            return
        try:
            with self.state.lock:
                self.post(data)
        except (OSError, http.client.HTTPException, ValueError):
            logging.exception("Could not apply authentication settings")
            self.reply(500, {"error": "Could not save settings or verify prerequisites; settings were not changed"})

    def post(self, data):
        if self.path in ("/auth/pin", "/auth/activity", "/auth/lock"):
            if not self.local or self.state.mode != "lockdown":
                self.reply(403, {"error": "Touchscreen PIN access is only available locally in Lockdown"})
                return
            self.state.prune()
            token = self.token(PIN_COOKIE)
            if self.path == "/auth/lock":
                if self.state.pin_sessions.pop(token, None) is not None:
                    self.state.revoke_connections()
                self.reply(200, {"ok": True}, cookie="", cookie_name=PIN_COOKIE)
            elif self.path == "/auth/activity":
                if token not in self.state.pin_sessions:
                    self.reply(401, {"error": "Touchscreen is locked"})
                else:
                    self.state.pin_sessions[token] = time.monotonic() + IDLE_SECONDS
                    self.reply(200, {"ok": True})
            elif not self.state.attempt("pin:" + self.headers["X-Real-IP"]):
                self.reply(429, {"error": "Too many PIN attempts. Wait 60 seconds and try again."})
            elif not self.state.verify_pin(data.get("pin", "")):
                self.reply(403, {"error": "Incorrect PIN"})
            else:
                self.reply(200, {"ok": True, "idle_seconds": IDLE_SECONDS},
                           cookie=self.state.session(kiosk=True), cookie_name=PIN_COOKIE)
            return
        if self.path == "/auth/logout":
            admin = self.state.sessions.pop(self.token(), None)
            pin = self.state.pin_sessions.pop(self.token(PIN_COOKIE), None)
            if admin is not None or pin is not None:
                self.state.revoke_connections()
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
            if self.local and self.state.mode == "lockdown":
                self.reply(403, {"error": "Use the touchscreen PIN to unlock this display"})
            elif self.state.record is None:
                self.reply(200, {"ok": True})
            elif not self.state.verify(data.get("password", "")):
                self.reply(403, {"error": "Incorrect password"})
            else:
                self.reply(200, {"ok": True}, cookie=self.state.session())
            return
        if self.state.record is not None and not self.state.verify(data.get("current", "")):
            self.reply(403, {"error": "Current password is incorrect"})
            return
        mode = data.get("mode", self.state.mode if self.state.record else "basic")
        if data.get("disable") is True:
            mode = "off"
        if mode not in ("off", "basic", "lockdown"):
            self.reply(400, {"error": "Choose Off, Basic or Lockdown"})
            return
        if mode == "off":
            self.state.save(None)
            self.reply(200, {"ok": True, "enabled": False}, cookie="")
            return
        password = data.get("new", "")
        keep_password = self.state.record is not None and not password and "mode" in data
        if not keep_password and len(password) < 4:
            self.reply(400, {"error": "Use a password of at least 4 characters"})
            return
        pin = self.state.record.get("pin") if self.state.record else None
        if data.get("pin"):
            if not data["pin"].isascii() or not data["pin"].isdigit() or len(data["pin"]) < 4:
                self.reply(400, {"error": "Use a touchscreen PIN of at least 4 digits"})
                return
            pin = credential(data["pin"])
        if mode == "lockdown":
            if pin is None:
                self.reply(400, {"error": "Set a separate touchscreen PIN before enabling Lockdown"})
                return
            if self.state.mode != "lockdown":
                readiness = lockdown_readiness()
                if not readiness["ready"]:
                    self.reply(409, {"error": readiness.get("error", "Configure an SSH recovery key first")})
                    return
        record = ({key: self.state.record[key] for key in ("salt", "hash")}
                  if keep_password else credential(password))
        record.update(mode=mode, pin=pin)
        self.state.save(record)
        self.reply(200, {"ok": True, "enabled": True, "mode": mode,
                         "reboot_required": self.state.reboot_required}, cookie=self.state.session())


class AuthHTTPServer(ThreadingHTTPServer):
    def service_actions(self):
        with self.state.lock:
            self.state.prune()


def serve():
    logging.basicConfig(level=logging.INFO)
    state = AuthState("/var/lib/re3d-auth/password.json")
    server = AuthHTTPServer(("127.0.0.1", 8090), AuthHandler)
    server.state = state
    server.serve_forever()


if __name__ == "__main__":
    serve()
