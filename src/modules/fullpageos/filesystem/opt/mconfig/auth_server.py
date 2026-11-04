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
import math
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit


COOKIE = "re3d_session"
HTTP_COOKIE = "re3d_http_session"
PIN_COOKIE = "re3d_kiosk"
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 5 * 60
APPLIED_MODE = Path("/run/re3d-security/mode")
MAX_ENTRIES = 4096
ROOT = Path(__file__).resolve().parent
AIRGAP_ENABLED = Path("/etc/re3d-airgap/enabled")
AIRGAP_APPLIED = Path("/run/re3d-airgap/enabled")


def airgap_status():
    enabled, applied = AIRGAP_ENABLED.exists(), AIRGAP_APPLIED.exists()
    return {"enabled": enabled, "applied": applied, "reboot_required": enabled != applied}


def change_airgap(enabled):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect("/run/re3d-airgap/settings.sock")
        connection.sendall(json.dumps({"enabled": enabled}).encode() + b"\n")
        with connection.makefile("rb") as stream:
            result = json.loads(stream.readline(1025))
    if (not isinstance(result, dict) or type(result.get("status")) is not int
            or result["status"] not in (200, 400, 500)
            or (result["status"] != 200 and not isinstance(result.get("error"), str))):
        raise ValueError("Invalid air-gap helper response")
    return result.pop("status"), result


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


def change_ssh_password(old, new):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect("/run/re3d-ssh-password/password.sock")
        connection.sendall(json.dumps({"old": old, "new": new}).encode() + b"\n")
        with connection.makefile("rb") as stream:
            result = json.loads(stream.readline(8193))
    if (not isinstance(result, dict) or type(result.get("status")) is not int
            or result["status"] not in (200, 400, 403, 500)
            or (result["status"] != 200 and not isinstance(result.get("error"), str))):
        raise ValueError("Invalid system password helper response")
    return result.pop("status"), result


def request_reboot():
    connection = http.client.HTTPConnection("127.0.0.1", 7125, timeout=5)
    try:
        connection.request("GET", "/printer/objects/query?print_stats&idle_timeout")
        response = connection.getresponse()
        data = json.loads(response.read(8192))
        if (response.status != 200 or not isinstance(data, dict)
                or not isinstance(data.get("result"), dict)
                or not isinstance(data["result"].get("status"), dict)):
            raise ValueError("Printer status could not be checked")
        status = data["result"]["status"]
        if not isinstance(status.get("print_stats"), dict) or not isinstance(status.get("idle_timeout"), dict):
            raise ValueError("Printer status is incomplete")
        print_state = status.get("print_stats", {}).get("state")
        idle_state = status.get("idle_timeout", {}).get("state")
        if print_state in ("printing", "paused") or idle_state == "Printing":
            return 409, {"error": "The printer is busy or a print is paused. Finish or cancel the print and wait until the printer is idle before rebooting."}
        if print_state not in ("standby", "complete", "cancelled", "error") or idle_state not in ("Idle", "Ready"):
            raise ValueError("Printer idle state could not be verified")
        connection.request("POST", "/machine/reboot")
        response = connection.getresponse()
        data = json.loads(response.read(8192))
        if response.status != 200 or not isinstance(data, dict) or data.get("result") != "ok":
            raise ValueError("The host reboot request was not accepted")
        return 200, {"ok": True}
    finally:
        connection.close()


def kiosk_printer_request(path, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", 7125, timeout=3)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        data = json.loads(response.read(65536))
        if response.status != 200 or not isinstance(data, dict) or "result" not in data:
            raise ValueError("The printer did not accept the request")
        return data["result"]
    finally:
        connection.close()


def kiosk_printer_status():
    result = kiosk_printer_request("/printer/objects/query?webhooks&print_stats&virtual_sdcard&heaters")
    if not isinstance(result, dict) or not isinstance(result.get("status"), dict):
        raise ValueError("Printer status is invalid")
    status = result["status"]
    if not all(isinstance(status.get(name), dict) for name in ("heaters", "print_stats", "webhooks", "virtual_sdcard")):
        raise ValueError("Printer status is incomplete")
    heaters = status.get("heaters", {}).get("available_heaters")
    if not isinstance(heaters, list) or not all(isinstance(name, str) for name in heaters):
        raise ValueError("Heater list is unavailable")
    temperatures = []
    if heaters:
        result = kiosk_printer_request("/printer/objects/query?"
                                       + "&".join(quote(name, safe="") for name in heaters))
        if not isinstance(result, dict) or not isinstance(result.get("status"), dict):
            raise ValueError("Heater status is invalid")
        for name in heaters:
            values = result["status"].get(name, {})
            if not isinstance(values, dict):
                raise ValueError("Heater status is invalid")
            temperature, target = values.get("temperature"), values.get("target")
            if not all(type(value) in (float, int) and math.isfinite(value) for value in (temperature, target)):
                raise ValueError("Heater temperatures are unavailable")
            temperatures.append({"name": name, "temperature": temperature, "target": target})
    print_state = status.get("print_stats", {}).get("state")
    firmware = status.get("webhooks", {}).get("state")
    progress = status.get("virtual_sdcard", {}).get("progress")
    if (not isinstance(print_state, str) or not isinstance(firmware, str)
            or type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 1):
        raise ValueError("Print status is unavailable")
    return {"state": print_state, "firmware": firmware, "progress": progress, "heaters": temperatures}


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
                if set(record) == {"mode", "pin"} and record["mode"] == "off":
                    validate_credential(record["pin"])
                    self.record = record
                    return
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
    def has_password(self):
        return self.record is not None and "salt" in self.record

    @property
    def touchscreen_locked(self):
        return self.mode == "lockdown" or AIRGAP_APPLIED.exists()

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
        return self.has_password and hmac.compare_digest(
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

    def reply(self, status, data, content_type="application/json", cookie=None, cookie_name=None,
              location=None, https_required=False):
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location is not None:
            self.send_header("Location", location)
        if https_required:
            self.send_header("X-Re3D-HTTPS-Required", "1")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                         "frame-src 'self' http://localhost:8081; "
                         "base-uri 'none'; form-action 'self'")
        if cookie is not None:
            cookie_name = cookie_name or self.session_cookie
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie",
                             f"{cookie_name}={cookie}; Path=/; HttpOnly; SameSite=Strict"
                             f"{secure}" + ("; Max-Age=0" if not cookie else ""))
        self.end_headers()
        self.wfile.write(body)

    @property
    def session_cookie(self):
        # A previous Secure cookie must not prevent Basic HTTP login from setting its own cookie.
        return COOKIE if self.headers.get("X-Forwarded-Proto") == "https" else HTTP_COOKIE

    def token(self, name=None):
        name = name or self.session_cookie
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return cookie[name].value if name in cookie else ""

    @property
    def local(self):
        return self.headers.get("X-Real-IP", "") in ("127.0.0.1", "::1")

    @property
    def remote_http(self):
        return not self.local and self.headers.get("X-Forwarded-Proto") != "https"

    def require_https(self):
        origin = self.headers.get("X-Re3D-HTTPS-Origin", "")
        try:
            parsed = urlsplit(origin)
            valid = (parsed.scheme == "https" and bool(parsed.netloc)
                     and not parsed.path and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            logging.error("Missing or invalid nginx HTTPS origin")
            self.reply(500, {"error": "HTTPS redirect is not configured correctly"})
            return
        self.reply(307, b"", location=origin + self.path)

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path == "/auth/printer-status":
            if not self.local or not self.state.touchscreen_locked:
                self.reply(403, {"error": "Locked-screen printer status is only available locally in a PIN-protected mode"})
                return
            try:
                self.reply(200, kiosk_printer_status())
            except (OSError, http.client.HTTPException, ValueError):
                logging.exception("Could not read locked-screen printer status")
                self.reply(503, {"error": "Live printer status unavailable. Temperatures and print state cannot be verified."})
            return
        with self.state.lock:
            if path == "/auth/check":
                if self.remote_http and self.state.mode == "lockdown":
                    self.reply(401, b"", https_required=True)
                    return
                self.state.prune()
                if self.local and self.state.touchscreen_locked:
                    allowed = self.token(PIN_COOKIE) in self.state.pin_sessions
                else:
                    allowed = not self.state.has_password or self.local or self.token() in self.state.sessions
                self.reply(204 if allowed else 401, b"")
            elif self.remote_http and (
                    self.state.mode == "lockdown"
                    or (self.state.mode == "off" and path in ("/auth/login", "/auth/settings"))
                    or (path == "/auth/settings" and parse_qs(url.query).get("mode") == ["lockdown"])):
                # Initial setup and Lockdown use TLS; Basic also permits unencrypted HTTP.
                self.require_https()
            elif path == "/auth/status":
                self.reply(200, {"enabled": self.state.has_password, "mode": self.state.mode,
                                "local": self.local, "has_pin": self.state.has_pin,
                                "touchscreen_locked": self.state.touchscreen_locked,
                                "airgap_enabled": AIRGAP_ENABLED.exists(),
                                "applied_mode": self.state.applied_mode,
                                "reboot_required": self.state.reboot_required})
            elif path == "/auth/airgap":
                self.reply(200, {**airgap_status(), "local": self.local,
                                 "has_password": self.state.has_password,
                                 "has_pin": self.state.has_pin, "security_mode": self.state.mode})
            elif path == "/auth/airgap-settings":
                self.reply(200, (ROOT / "airgap.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/auth/kiosk-status" and self.local:
                self.state.prune()
                self.reply(200, {"unlocked": self.token(PIN_COOKIE) in self.state.pin_sessions,
                                "mode": self.state.mode, "idle_seconds": IDLE_SECONDS,
                                "touchscreen_locked": self.state.touchscreen_locked})
            elif path == "/auth/kiosk" and self.local:
                self.reply(200, (ROOT / "kiosk.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/auth/kiosk-client.js":
                body = ((ROOT / "kiosk-client.js").read_bytes()
                        if self.local and self.state.touchscreen_locked else b"")
                self.reply(200, body, "application/javascript")
            elif path == "/auth/security.css":
                self.reply(200, (ROOT / "security.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/auth/fonts/Lato-Regular.ttf":
                self.reply(200, (ROOT / "www/fonts/Lato-Regular.ttf").read_bytes(), "font/ttf")
            elif path == "/auth/logo.png":
                self.reply(200, (ROOT / "www/img/re3d-logo.png").read_bytes(), "image/png")
            elif path in ("/auth/login", "/auth/settings"):
                self.reply(200, (ROOT / "auth.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if self.remote_http and self.state.mode != "basic":
            self.reply(426, {"error": "Use HTTPS before submitting passwords or changing security settings"})
            return
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
            for key in ("password", "current", "new", "pin", "pin_confirm", "current_pin", "ssh_current", "ssh_new"):
                if key in data and (not isinstance(data[key], str) or len(data[key]) > 1024):
                    raise ValueError("Passwords must be strings of at most 1024 characters")
        except (ValueError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})
            return
        if self.path == "/auth/emergency-stop":
            if not self.local or not self.state.touchscreen_locked:
                self.reply(403, {"error": "Locked-screen emergency stop is only available locally in a PIN-protected mode"})
                return
            try:
                if kiosk_printer_request("/printer/emergency_stop", "POST") != "ok":
                    raise ValueError("Emergency stop was not acknowledged")
                self.reply(200, {"ok": True})
            except (OSError, http.client.HTTPException, ValueError):
                logging.exception("Could not confirm touchscreen emergency stop")
                self.reply(503, {"error": "Emergency stop NOT confirmed. Use the physical emergency stop or disconnect printer power if unsafe."})
            return
        try:
            with self.state.lock:
                self.post(data)
        except (OSError, http.client.HTTPException, ValueError):
            logging.exception("Could not apply authentication settings")
            error = ("Could not verify that the printer is idle or confirm the reboot request. "
                     "Check the printer connection before trying again; your saved settings are unchanged."
                     if self.path == "/auth/reboot"
                     else "Could not check Lockdown requirements. Check that the Configurator service is running, then try again."
                     if self.path == "/auth/readiness"
                     else "Could not save air-gap settings. Check re3d-airgap-helper.service logs and reload to verify the saved state."
                     if self.path == "/auth/airgap"
                     else "Could not save settings or verify prerequisites; settings were not changed")
            self.reply(500, {"error": error})

    def post(self, data):
        if self.remote_http and (self.state.mode != "basic" or (
                self.path == "/auth/password" and data.get("mode") == "lockdown")):
            self.reply(426, {"error": "Use HTTPS before configuring Lockdown or submitting passwords outside Basic mode"})
            return
        if self.path in ("/auth/pin", "/auth/activity", "/auth/lock"):
            if not self.local or not self.state.touchscreen_locked:
                self.reply(403, {"error": "Touchscreen PIN access is only available locally in a PIN-protected mode"})
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
            admin = self.state.sessions.pop(self.token(COOKIE), None)
            http_admin = self.state.sessions.pop(self.token(HTTP_COOKIE), None)
            pin = self.state.pin_sessions.pop(self.token(PIN_COOKIE), None)
            if admin is not None or http_admin is not None or pin is not None:
                self.state.revoke_connections()
            self.reply(200, {"ok": True}, cookie="")
            return
        if self.path not in ("/auth/login", "/auth/password", "/auth/ssh-password", "/auth/readiness", "/auth/reboot", "/auth/airgap"):
            self.reply(404, {"error": "Not found"})
            return
        address = self.headers.get("X-Real-IP", "")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            self.reply(400, {"error": "Missing client address"})
            return
        if not self.state.attempt("readiness:" + address if self.path == "/auth/readiness" else address):
            self.reply(429, {"error": "Too many authentication attempts. Wait 60 seconds and try again."})
            return
        if self.path == "/auth/reboot":
            airgap_pending = airgap_status()["reboot_required"]
            pin_session = self.local and self.token(PIN_COOKIE) in self.state.pin_sessions
            admin_session = self.token() in self.state.sessions
            if airgap_pending and not (admin_session or pin_session):
                self.reply(403, {"error": "Verify the touchscreen PIN in Air-gap settings before rebooting"})
            elif self.state.has_password and not (admin_session or airgap_pending and pin_session):
                self.reply(403, {"error": "Sign in with the web administrator password before rebooting"})
            elif not self.state.reboot_required and not airgap_status()["reboot_required"]:
                self.reply(409, {"error": "These security settings do not require a reboot"})
            else:
                status, result = request_reboot()
                self.reply(status, result)
            return
        if self.path == "/auth/login":
            if self.local and self.state.touchscreen_locked:
                self.reply(403, {"error": "Use the touchscreen PIN to unlock this display"})
            elif not self.state.has_password:
                self.reply(200, {"ok": True})
            elif not self.state.verify(data.get("password", "")):
                self.reply(403, {"error": "Incorrect password"})
            else:
                self.reply(200, {"ok": True}, cookie=self.state.session())
            return
        if self.path == "/auth/airgap":
            if not self.local:
                self.reply(403, {"error": "Change air-gap mode on the printer touchscreen, not a remote browser"})
            elif type(data.get("enabled")) is not bool:
                self.reply(400, {"error": "Air-gap enabled must be a boolean"})
            else:
                pin = data.get("pin", "")
                if self.state.has_pin:
                    if not self.state.verify_pin(pin):
                        self.reply(403, {"error": "Incorrect touchscreen PIN"})
                        return
                else:
                    if not data["enabled"]:
                        self.reply(409, {"error": "No touchscreen PIN is configured. Create one when enabling air-gapped mode."})
                        return
                    if not pin.isascii() or not pin.isdigit() or len(pin) < 4 or pin != data.get("pin_confirm"):
                        self.reply(400, {"error": "Create and confirm a touchscreen PIN of at least 4 digits"})
                        return
                    record = dict(self.state.record) if self.state.record else {}
                    record.update(mode=self.state.mode, pin=credential(pin))
                    self.state.save(record)
                status, result = change_airgap(data["enabled"])
                self.reply(status, {**result, "has_pin": self.state.has_pin,
                                    "has_password": self.state.has_password, "security_mode": self.state.mode},
                           cookie=self.state.session(kiosk=True) if status == 200 else None, cookie_name=PIN_COOKIE)
            return
        if self.state.has_password and not self.state.verify(data.get("current", "")):
            self.reply(403, {"error": "Current password is incorrect"})
            return
        if (self.path == "/auth/password" and not self.state.has_password and self.state.has_pin
                and not self.state.verify_pin(data.get("current_pin", ""))):
            self.reply(403, {"error": "The existing touchscreen PIN is required before configuring administrator protection"})
            return
        if self.path == "/auth/readiness":
            self.reply(200, lockdown_readiness())
            return
        if self.path == "/auth/ssh-password":
            status, result = change_ssh_password(data.get("ssh_current", ""), data.get("ssh_new", ""))
            self.reply(status, result)
            return
        mode = data.get("mode", self.state.mode if self.state.has_password else "basic")
        if data.get("disable") is True:
            mode = "off"
        if mode not in ("off", "basic", "lockdown"):
            self.reply(400, {"error": "Choose Off, Basic or Lockdown"})
            return
        if mode == "off":
            self.state.save({"mode": "off", "pin": self.state.record["pin"]} if self.state.has_pin else None)
            self.reply(200, {"ok": True, "enabled": False}, cookie="")
            return
        password = data.get("new", "")
        keep_password = self.state.has_password and not password and "mode" in data
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
                    self.reply(409, {"error": readiness.get("error", "Set a non-default pi password first")})
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
