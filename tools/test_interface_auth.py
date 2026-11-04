import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, PropertyMock, patch


FILESYSTEM = Path(__file__).resolve().parents[1] / "src/modules/fullpageos/filesystem"
ASSETS = FILESYSTEM / "opt/mconfig"
spec = importlib.util.spec_from_file_location("interface_auth", ASSETS / "auth_server.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)
PASSWORD = "a test password that is long"
NEW_PASSWORD = "another long test password"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = auth.AuthState(Path(self.directory.name) / "password.json")
        for name in ("AIRGAP_ENABLED", "AIRGAP_APPLIED"):
            patcher = patch.object(auth, name, Path(self.directory.name) / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = auth.ThreadingHTTPServer(("127.0.0.1", 0), auth.AuthHandler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, cookie="", address="192.168.1.2", **headers):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        self.addCleanup(connection.close)
        defaults = {"Host": "printer.local", "X-Forwarded-Proto": "https",
                    "Origin": "https://printer.local", "X-Real-IP": address,
                    "Content-Type": "application/json", "Cookie": cookie}
        defaults.update(headers)
        connection.request("POST" if data is not None else "GET", "/auth/" + path,
                           json.dumps(data) if data is not None else None, defaults)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body) if body and response.getheader(
            "Content-Type") == "application/json" else body, response.getheader("Set-Cookie")

    def enable(self):
        status, _, cookie = self.request("password", {"new": PASSWORD})
        self.assertEqual(status, 200)
        return cookie.split(";")[0]

    def test_airgap_changes_require_a_local_administrator_password(self):
        self.assertEqual(self.request("airgap", {"enabled": True}, address="127.0.0.1")[0], 409)
        self.enable()
        self.assertEqual(self.request("password", {"current": PASSWORD, "mode": "basic", "pin": "1234"})[0], 200)
        with patch.object(auth, "change_airgap", return_value=(200, {"ok": True})) as change:
            self.assertEqual(self.request("airgap", {"enabled": True, "current": PASSWORD})[0], 403)
            self.assertEqual(self.request("airgap", {"enabled": True, "current": "wrong"},
                                          address="127.0.0.1")[0], 403)
            change.assert_not_called()
            status, _, cookie = self.request("airgap", {"enabled": True, "current": PASSWORD},
                                             address="127.0.0.1")
            self.assertEqual(status, 200)
            self.assertIn("re3d_session=", cookie)
            change.assert_called_once_with(True)

    def test_airgap_blocks_password_removal_until_isolation_has_ended(self):
        self.enable()
        for marker in (auth.AIRGAP_ENABLED, auth.AIRGAP_APPLIED):
            marker.touch()
            self.assertEqual(self.request("password", {"disable": True, "current": PASSWORD})[0], 409)
            self.assertIsNotNone(self.state.record)
            marker.unlink()
        self.assertEqual(self.request("password", {"disable": True, "current": PASSWORD})[0], 200)

    def test_airgap_status_distinguishes_pending_enable_and_pending_disable(self):
        for enabled, applied in ((False, False), (True, False), (True, True), (False, True)):
            for path, present in ((auth.AIRGAP_ENABLED, enabled), (auth.AIRGAP_APPLIED, applied)):
                path.touch() if present else path.unlink(missing_ok=True)
            status, body, _ = self.request("airgap", address="127.0.0.1")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"enabled": enabled, "applied": applied,
                                   "reboot_required": enabled != applied,
                                   "local": True, "has_password": False,
                                   "has_pin": False, "security_mode": "off"})

    def test_airgap_requires_a_shared_pin_and_reuses_it_without_replacing_credentials(self):
        self.enable()
        with patch.object(auth, "change_airgap", return_value=(200, {"ok": True})) as change:
            self.assertEqual(self.request("airgap", {"enabled": True, "current": PASSWORD},
                                          address="127.0.0.1")[0], 409)
            change.assert_not_called()
            self.assertEqual(self.request("password", {"current": PASSWORD, "mode": "basic", "pin": "1234"})[0], 200)
            pin = self.state.record["pin"].copy()
            self.assertEqual(self.request("airgap", {"enabled": True, "current": PASSWORD},
                                          address="127.0.0.1")[0], 200)
            self.assertEqual(self.state.record["pin"], pin)
            self.assertTrue(self.state.verify_pin("1234"))
            self.assertEqual(self.state.mode, "basic")

    def test_airgap_pin_protects_local_control_and_stays_required_until_reboot_after_disable(self):
        self.enable()
        self.request("password", {"current": PASSWORD, "mode": "basic", "pin": "1234"})
        auth.AIRGAP_APPLIED.touch()
        self.assertEqual(self.request("check", address="127.0.0.1")[0], 401)
        status, _, cookie = self.request("pin", {"pin": "1234"}, address="127.0.0.1")
        self.assertEqual(status, 200)
        self.assertEqual(self.request("check", address="127.0.0.1", cookie=cookie)[0], 204)
        self.assertTrue(self.request("status", address="127.0.0.1")[1]["touchscreen_locked"])
        self.assertTrue(self.request("kiosk-status", address="127.0.0.1")[1]["touchscreen_locked"])
        self.assertTrue(self.request("kiosk-client.js", address="127.0.0.1")[1])
        self.assertEqual(self.request("lock", {}, address="127.0.0.1", cookie=cookie)[0], 200)
        self.assertEqual(self.request("check", address="127.0.0.1", cookie=cookie)[0], 401)
        with patch.object(auth, "change_airgap", return_value=(200, {"ok": True})):
            self.assertEqual(self.request("airgap", {"enabled": False, "current": PASSWORD},
                                          address="127.0.0.1")[0], 200)
        self.assertTrue(self.state.touchscreen_locked)

    def test_airgap_locked_screen_preserves_telemetry_and_emergency_stop(self):
        self.enable()
        auth.AIRGAP_APPLIED.touch()
        with patch.object(auth, "kiosk_printer_status", return_value={"state": "printing"}), \
                patch.object(auth, "kiosk_printer_request", return_value="ok"):
            self.assertEqual(self.request("printer-status", address="127.0.0.1")[0], 200)
            self.assertEqual(self.request("emergency-stop", {}, address="127.0.0.1")[0], 200)
            self.assertEqual(self.request("printer-status")[0], 403)

    def test_airgap_changes_require_same_origin_and_strict_boolean(self):
        self.enable()
        with patch.object(auth, "change_airgap") as change:
            self.assertEqual(self.request("airgap", {"enabled": True, "current": PASSWORD},
                                          address="127.0.0.1", Origin="https://attacker.example")[0], 403)
            self.assertEqual(self.request("airgap", {"enabled": "true", "current": PASSWORD},
                                          address="127.0.0.1")[0], 400)
            change.assert_not_called()

    def test_airgap_helper_errors_are_reported_without_claiming_success(self):
        self.enable()
        with patch.object(auth, "change_airgap", side_effect=OSError("unavailable")), \
                self.assertLogs(level="ERROR"):
            status, body, _ = self.request("airgap", {"enabled": False, "current": PASSWORD},
                                           address="127.0.0.1")
        self.assertEqual(status, 500)
        self.assertIn("reload to verify", body["error"])

    def test_pending_airgap_policy_can_reboot_with_an_administrator_session(self):
        cookie = self.enable()
        auth.AIRGAP_ENABLED.touch()
        with patch.object(auth, "request_reboot", return_value=(200, {"ok": True})) as reboot:
            self.assertEqual(self.request("reboot", {}, address="127.0.0.1")[0], 403)
            self.assertEqual(self.request("reboot", {}, address="127.0.0.1", cookie=cookie)[0], 200)
            reboot.assert_called_once()

    def test_locked_screen_status_and_stop_are_local_only_without_a_pin_session(self):
        self.lockdown()
        self.state.sessions.clear()
        with patch.object(auth, "kiosk_printer_status", return_value={"state": "printing"}) as status, \
                patch.object(auth, "kiosk_printer_request", return_value="ok") as stop:
            self.assertEqual(self.request("printer-status")[0], 403)
            self.assertEqual(self.request("emergency-stop", {})[0], 403)
            status.assert_not_called()
            stop.assert_not_called()
            self.assertEqual(self.request("printer-status", address="127.0.0.1")[0], 200)
            self.assertEqual(self.request("emergency-stop", {}, address="127.0.0.1")[0], 200)
            stop.assert_called_once_with("/printer/emergency_stop", "POST")
            self.assertFalse(self.state.pin_sessions)
            self.assertFalse(self.state.sessions)

    def test_stop_requires_same_origin_json_and_reports_printer_failure(self):
        self.lockdown()
        with patch.object(auth, "kiosk_printer_request", side_effect=OSError("Printer offline")) as stop:
            self.assertEqual(self.request("emergency-stop", {}, address="127.0.0.1",
                                          Origin="https://attacker.example")[0], 403)
            self.assertEqual(self.request("emergency-stop", {}, address="127.0.0.1",
                                          **{"Content-Type": "text/plain"})[0], 415)
            stop.assert_not_called()
            with self.assertLogs(level="ERROR"):
                status, body, _ = self.request("emergency-stop", {}, address="127.0.0.1")
            self.assertEqual(status, 503)
            self.assertIn("NOT confirmed", body["error"])
            self.assertIn("physical emergency stop", body["error"])

    def test_locked_screen_status_failure_is_explicit_and_does_not_extend_pin_session(self):
        self.lockdown()
        token = self.state.session(kiosk=True)
        expiry = self.state.pin_sessions[token]
        with patch.object(auth, "kiosk_printer_status", side_effect=ValueError("Invalid status")), \
                self.assertLogs(level="ERROR"):
            status, body, _ = self.request("printer-status", cookie=f"re3d_kiosk={token}", address="127.0.0.1")
        self.assertEqual(status, 503)
        self.assertIn("unavailable", body["error"])
        self.assertEqual(self.state.pin_sessions[token], expiry)

    def test_printer_status_queries_all_configured_heaters_and_limits_output(self):
        base = {"status": {"webhooks": {"state": "ready"}, "print_stats": {"state": "printing", "filename": "private.gcode"},
                           "virtual_sdcard": {"progress": .25},
                           "heaters": {"available_heaters": ["extruder", "heater_bed", "heater_generic chamber"]}}}
        heaters = {"status": {name: {"temperature": 42.5, "target": 60}
                              for name in base["status"]["heaters"]["available_heaters"]}}
        with patch.object(auth, "kiosk_printer_request", side_effect=[base, heaters]) as request:
            result = auth.kiosk_printer_status()
        self.assertEqual(result["progress"], .25)
        self.assertEqual(len(result["heaters"]), 3)
        self.assertNotIn("filename", result)
        self.assertIn("heater_generic%20chamber", request.call_args.args[0])

    def test_locked_screen_endpoints_do_not_work_in_off_or_basic_modes(self):
        for enabled in (False, True):
            if enabled:
                self.enable()
            with patch.object(auth, "kiosk_printer_status") as status, \
                    patch.object(auth, "kiosk_printer_request") as stop:
                self.assertEqual(self.request("printer-status", address="127.0.0.1")[0], 403)
                self.assertEqual(self.request("emergency-stop", {}, address="127.0.0.1")[0], 403)
                status.assert_not_called()
                stop.assert_not_called()

    def test_printer_status_rejects_malformed_and_nonfinite_temperatures(self):
        for invalid in (None, {}, {"status": {"heaters": None}}):
            with patch.object(auth, "kiosk_printer_request", return_value=invalid):
                self.assertRaises(ValueError, auth.kiosk_printer_status)
        base = {"status": {"webhooks": {"state": "ready"}, "print_stats": {"state": "standby"},
                           "virtual_sdcard": {"progress": 0}, "heaters": {"available_heaters": ["extruder"]}}}
        for temperature in (None, float("nan"), float("inf"), "25", True):
            with patch.object(auth, "kiosk_printer_request", side_effect=[
                    base, {"status": {"extruder": {"temperature": temperature, "target": 0}}}]):
                self.assertRaises(ValueError, auth.kiosk_printer_status)

    def test_stop_without_acknowledgement_is_never_reported_as_success(self):
        self.lockdown()
        with patch.object(auth, "kiosk_printer_request", return_value={"error": "Printer unavailable"}), \
                self.assertLogs(level="ERROR"):
            status, body, _ = self.request("emergency-stop", {}, address="127.0.0.1")
        self.assertEqual(status, 503)
        self.assertIn("NOT confirmed", body["error"])

    def test_security_reboot_requires_an_admin_session_and_pending_policy(self):
        cookie = self.enable()
        with patch.object(auth, "request_reboot", return_value=(200, {"ok": True})) as reboot:
            self.assertEqual(self.request("reboot", {}, cookie=cookie)[0], 409)
            with patch.object(auth.AuthState, "reboot_required", new_callable=PropertyMock, return_value=True):
                self.assertEqual(self.request("reboot", {})[0], 403)
                self.assertEqual(self.request("reboot", {}, address="127.0.0.1")[0], 403)
                pin = "re3d_kiosk=" + self.state.session(kiosk=True)
                self.assertEqual(self.request("reboot", {}, cookie=pin)[0], 403)
                reboot.assert_not_called()
                self.assertEqual(self.request("reboot", {}, cookie=cookie)[0], 200)
                reboot.assert_called_once()

    def test_security_reboot_errors_and_origin_are_explicit(self):
        with patch.object(auth.AuthState, "reboot_required", new_callable=PropertyMock, return_value=True), \
                patch.object(auth, "request_reboot", side_effect=OSError("Printer offline")) as reboot:
            self.assertEqual(self.request("reboot", {}, Origin="https://attacker.example")[0], 403)
            self.assertEqual(self.request("reboot", {}, **{"X-Forwarded-Proto": "http"})[0], 426)
            reboot.assert_not_called()
            with self.assertLogs(level="ERROR"):
                status, body, _ = self.request("reboot", {})
            self.assertEqual(status, 500)
            self.assertIn("Could not verify", body["error"])

    def test_reboot_checks_printer_state_and_only_requests_an_idle_host_reboot(self):
        for print_state, idle_state, expected in (
                ("printing", "Printing", 409), ("paused", "Ready", 409),
                ("standby", "Printing", 409), ("complete", "Idle", 200), ("standby", "Ready", 200)):
            with self.subTest(print_state=print_state, idle_state=idle_state):
                status_response = Mock(status=200)
                status_response.read.return_value = json.dumps({"result": {"status": {
                    "print_stats": {"state": print_state}, "idle_timeout": {"state": idle_state}}}}).encode()
                reboot_response = Mock(status=200)
                reboot_response.read.return_value = b'{"result":"ok"}'
                connection = Mock()
                connection.getresponse.side_effect = [status_response, reboot_response]
                with patch.object(auth.http.client, "HTTPConnection", return_value=connection):
                    self.assertEqual(auth.request_reboot()[0], expected)
                if expected == 200:
                    connection.request.assert_any_call("POST", "/machine/reboot")
                else:
                    self.assertEqual(connection.request.call_count, 1)
                connection.close.assert_called_once()

    def test_invalid_printer_status_never_triggers_reboot(self):
        for data in ({}, {"result": {"status": {}}}, {"result": None}, None):
            connection = Mock()
            response = Mock(status=200)
            response.read.return_value = json.dumps(data).encode()
            connection.getresponse.return_value = response
            with patch.object(auth.http.client, "HTTPConnection", return_value=connection):
                self.assertRaises(ValueError, auth.request_reboot)
            self.assertEqual(connection.request.call_count, 1)

    def test_readiness_checks_require_admin_confirmation_and_use_existing_prerequisites(self):
        with patch.object(auth, "lockdown_readiness", return_value={"ready": False, "error": "Change pi password"}) as check:
            status, body, _ = self.request("readiness", {})
            self.assertEqual(status, 200)
            self.assertFalse(body["ready"])
            self.enable()
            check.reset_mock()
            self.assertEqual(self.request("readiness", {})[0], 403)
            check.assert_not_called()
            status, body, _ = self.request("readiness", {"current": PASSWORD})
            self.assertEqual(status, 200)
            self.assertEqual(body["error"], "Change pi password")
            check.assert_called_once()

    def test_readiness_failure_is_explicit_and_never_looks_ready(self):
        with patch.object(auth, "lockdown_readiness", side_effect=OSError("Backend unavailable")), \
                self.assertLogs(level="ERROR"):
            status, body, _ = self.request("readiness", {})
        self.assertEqual(status, 500)
        self.assertIn("error", body)
        self.assertNotIn("ready", body)

    def test_ssh_password_changes_require_current_admin_password_when_enabled(self):
        data = {"ssh_current": "raspberry", "ssh_new": "new pi password"}
        with patch.object(auth, "change_ssh_password", return_value=(200, {"ok": True})) as change:
            self.assertEqual(self.request("ssh-password", data)[0], 200)
            change.assert_called_once_with("raspberry", "new pi password")
            change.reset_mock()
            cookie = self.enable()
            self.assertEqual(self.request("ssh-password", data, cookie=cookie)[0], 403)
            self.assertEqual(self.request("ssh-password", data, address="127.0.0.1")[0], 403)
            change.assert_not_called()
            self.assertEqual(self.request("ssh-password", {**data, "current": PASSWORD})[0], 200)

    def test_ssh_password_changes_enforce_transport_origin_and_rate_limits(self):
        data = {"ssh_current": "raspberry", "ssh_new": "new pi password"}
        with patch.object(auth, "change_ssh_password", return_value=(403, {"error": "Wrong pi password"})) as change:
            self.assertEqual(self.request("ssh-password", data, **{
                "X-Forwarded-Proto": "http", "Origin": "http://printer.local"})[0], 426)
            self.assertEqual(self.request("ssh-password", data, Origin="https://attacker.example")[0], 403)
            change.assert_not_called()
            for _ in range(5):
                self.assertEqual(self.request("ssh-password", data)[0], 403)
            self.assertEqual(self.request("ssh-password", data)[0], 429)
            self.assertEqual(change.call_count, 5)

    def test_ssh_password_helper_failure_is_reported(self):
        with patch.object(auth, "change_ssh_password", side_effect=OSError("Helper unavailable")), \
                self.assertLogs(level="ERROR"):
            status, body, _ = self.request("ssh-password", {
                "ssh_current": "raspberry", "ssh_new": "new pi password"})
        self.assertEqual(status, 500)
        self.assertIn("error", body)

    def test_password_lifecycle_and_current_password_required_even_locally(self):
        self.assertEqual(self.request("check")[0], 204)
        cookie = self.enable()
        self.assertEqual(self.request("check")[0], 401)
        self.assertEqual(self.request("check", cookie=cookie)[0], 204)
        for data in ({"new": NEW_PASSWORD}, {"current": "wrong", "disable": True}):
            self.assertEqual(self.request("password", data, address="127.0.0.1")[0], 403)
        status, _, changed = self.request("password", {"current": PASSWORD, "new": NEW_PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("check", cookie=cookie)[0], 401)
        self.assertEqual(self.request("check", cookie=changed.split(";")[0])[0], 204)
        # A session cookie alone cannot disable protection.
        self.assertEqual(self.request("password", {"disable": True}, cookie=changed)[0], 403)
        self.assertEqual(self.request("password", {"current": NEW_PASSWORD, "disable": True},
                                      address="192.168.1.3")[0], 200)
        self.assertEqual(self.request("check")[0], 204)
        self.assertIsNone(auth.AuthState(self.state.path).record)

    def test_login_logout_and_secure_cookie(self):
        self.enable()
        self.assertEqual(self.request("login", {"password": "wrong"})[0], 403)
        status, _, cookie = self.request("login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        for attribute in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(attribute, cookie)
        token = cookie.split(";")[0]
        self.assertEqual(self.request("check", cookie=token)[0], 204)
        self.assertEqual(self.request("logout", {}, cookie=token)[0], 200)
        self.assertEqual(self.request("check", cookie=token)[0], 401)

    def test_local_bypass_does_not_trust_forwarded_client_input(self):
        self.enable()
        for address, expected in (("127.0.0.1", 204), ("::1", 204),
                                  ("127.0.0.2", 401), ("192.168.1.2", 401)):
            self.assertEqual(self.request("check", address=address,
                                          **{"X-Forwarded-For": "127.0.0.1"})[0], expected)

    def test_persistence_permissions_and_no_plaintext(self):
        cookie = self.enable()
        content = self.state.path.read_text()
        self.assertNotIn(PASSWORD, content)
        loaded = auth.AuthState(self.state.path)
        self.assertTrue(loaded.verify(PASSWORD))
        self.assertFalse(loaded.verify("wrong"))
        self.assertEqual(loaded.sessions, {})
        self.assertTrue(cookie)
        if os.name != "nt":
            self.assertEqual(self.state.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.state.path.parent.glob(".auth-*")), [])

    def test_corrupt_state_fails_closed(self):
        for value in ("invalid json", "false", "{}", '{"salt":"xx","hash":"xx"}'):
            self.state.path.write_text(value)
            with self.assertRaises((ValueError, TypeError)):
                auth.AuthState(self.state.path)

    def test_csrf_validation_and_short_password(self):
        self.assertEqual(self.request("password", {"new": PASSWORD},
                                      Origin="https://attacker.example")[0], 403)
        self.assertEqual(self.request("password", {"new": PASSWORD},
                                      **{"Content-Type": "text/plain"})[0], 415)
        for value in ("abc", 123, ["password"]):
            self.assertEqual(self.request("password", {"new": value})[0], 400)
        self.assertEqual(self.request("password", {"new": "x" * 1025})[0], 400)
        self.assertIsNone(self.state.record)

    def test_four_character_minimum_for_enabling_and_changing_password(self):
        for password in ("", "a", "ab", "abc"):
            with self.subTest(password=password):
                self.assertEqual(self.request("password", {"new": password})[0], 400)
                self.assertIsNone(self.state.record)
        self.assertEqual(self.request("password", {"new": "1234"})[0], 200)
        self.assertTrue(self.state.verify("1234"))
        self.assertEqual(self.request("password", {"current": "1234", "new": "abcd"},
                                      address="192.168.1.3")[0], 200)
        self.assertTrue(self.state.verify("abcd"))
        page = (ASSETS / "auth.html").read_text()
        self.assertIn('id="new" type="password" autocomplete="new-password" minlength="4"', page)
        self.assertIn('id="confirm" type="password" autocomplete="new-password" minlength="4"', page)
        self.assertIn("at least 4 characters", page)

    def test_five_attempts_per_minute_and_expired_sessions(self):
        cookie = self.enable()
        for _ in range(5):
            self.assertEqual(self.request("login", {"password": "wrong"},
                                          address="192.168.1.3")[0], 403)
        self.assertEqual(self.request("login", {"password": PASSWORD},
                                      address="192.168.1.3")[0], 429)
        with patch.object(auth.time, "monotonic", return_value=time.monotonic() + 61):
            self.assertEqual(self.request("login", {"password": PASSWORD},
                                          address="192.168.1.3")[0], 200)
        with patch.object(auth.time, "monotonic", return_value=time.monotonic() + auth.SESSION_SECONDS + 1):
            self.assertEqual(self.request("check", cookie=cookie)[0], 401)

    def test_failed_write_preserves_current_password(self):
        self.enable()
        with patch.object(auth.os, "replace", side_effect=OSError("test failure")), \
                self.assertLogs(level="ERROR"):
            self.assertEqual(self.request("password", {"current": PASSWORD, "new": NEW_PASSWORD})[0], 500)
        self.assertTrue(self.state.verify(PASSWORD))
        self.assertTrue(auth.AuthState(self.state.path).verify(PASSWORD))

    def test_auth_pages_do_not_include_passwords(self):
        self.enable()
        for page in ("login", "settings"):
            status, body, _ = self.request(page)
            self.assertEqual(status, 200)
            self.assertNotIn(PASSWORD.encode(), body)

    def test_theme_and_logo_load_without_a_session_when_interfaces_are_locked(self):
        self.enable()
        status, css, _ = self.request("security.css")
        self.assertEqual(status, 200)
        self.assertIn(b"--brand: #FFD400", css)
        self.assertNotIn(PASSWORD.encode(), css)
        status, logo, _ = self.request("logo.png")
        self.assertEqual(status, 200)
        self.assertTrue(logo.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_lato_font_loads_without_an_admin_or_pin_session_in_lockdown(self):
        self.lockdown()
        self.state.sessions.clear()
        font = (ASSETS / "www/fonts/Lato-Regular.ttf").read_bytes()
        for address, proto in (("192.168.1.2", "https"), ("127.0.0.1", "http")):
            with self.subTest(address=address):
                status, body, cookie = self.request("fonts/Lato-Regular.ttf", address=address,
                                                    **{"X-Forwarded-Proto": proto})
                self.assertEqual(status, 200)
                self.assertEqual(body, font)
                self.assertIsNone(cookie)
                self.assertEqual(self.request("check", address=address,
                                              **{"X-Forwarded-Proto": proto})[0], 401)
        for path in ("fonts/OFL.txt", "fonts/../auth_server.py", "fonts/%2e%2e/auth_server.py"):
            self.assertEqual(self.request(path)[0], 404)

    def test_http_is_available_when_off_but_remote_credentials_require_tls(self):
        self.assertEqual(self.request("check", **{"X-Forwarded-Proto": "http"})[0], 204)
        status, _, _ = self.request("password", {"new": PASSWORD},
                                    **{"X-Forwarded-Proto": "http", "Origin": "http://printer.local"})
        self.assertEqual(status, 426)
        self.assertIsNone(self.state.record)
        self.enable()
        self.assertEqual(self.request("check", **{"X-Forwarded-Proto": "http"})[0], 401)
        self.assertEqual(self.request("check", address="127.0.0.1",
                                      **{"X-Forwarded-Proto": "http"})[0], 204)

    def test_basic_http_login_settings_password_change_and_logout(self):
        secure_cookie = self.enable()
        headers = {"X-Forwarded-Proto": "http", "Origin": "http://printer.local"}
        for page in ("login", "settings", "status"):
            self.assertEqual(self.request(page, **headers)[0], 200)
        self.assertEqual(self.request("check", cookie=secure_cookie, **headers)[0], 401)
        self.assertEqual(self.request("login", {"password": "wrong"}, **headers)[0], 403)
        status, _, cookie = self.request("login", {"password": PASSWORD}, **headers)
        self.assertEqual(status, 200)
        self.assertTrue(cookie.startswith(auth.HTTP_COOKIE + "="))
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly; SameSite=Strict", cookie)
        token = cookie.split(";")[0]
        self.assertEqual(self.request("check", cookie=token, **headers)[0], 204)
        self.assertEqual(self.request("password", {"current": "wrong", "new": NEW_PASSWORD},
                                      **headers)[0], 403)
        status, _, changed = self.request("password", {"current": PASSWORD, "new": NEW_PASSWORD},
                                          **headers)
        self.assertEqual(status, 200)
        self.assertTrue(self.state.verify(NEW_PASSWORD))
        self.assertEqual(self.request("check", cookie=token, **headers)[0], 401)
        token = changed.split(";")[0]
        self.assertEqual(self.request("check", cookie=token, **headers)[0], 204)
        self.assertEqual(self.request("logout", {}, cookie=token, **headers)[0], 200)
        self.assertEqual(self.request("check", cookie=token, **headers)[0], 401)
        self.assertEqual(self.request("password", {"current": NEW_PASSWORD, "disable": True},
                                      address="192.168.1.3", **headers)[0], 200)
        self.assertIsNone(self.state.record)

    def test_basic_http_keeps_origin_validation_and_cannot_enable_lockdown(self):
        self.enable()
        headers = {"X-Forwarded-Proto": "http", "Origin": "http://printer.local"}
        self.assertEqual(self.request("login", {"password": PASSWORD},
                                      **{**headers, "Origin": "http://attacker.example"})[0], 403)
        with patch.object(auth, "lockdown_readiness") as readiness:
            status, _, _ = self.request("password", {"current": PASSWORD, "mode": "lockdown",
                                                     "pin": "1234"}, **headers)
        self.assertEqual(status, 426)
        self.assertEqual(self.state.mode, "basic")
        readiness.assert_not_called()
        self.assertEqual(self.request("settings?mode=lockdown", **{
            **headers, "X-Re3D-HTTPS-Origin": "https://printer.local"})[0], 307)

    def test_lockdown_still_rejects_remote_http_even_with_an_admin_session(self):
        self.lockdown()
        headers = {"X-Forwarded-Proto": "http", "Origin": "http://printer.local"}
        self.assertEqual(self.request("check", **{"X-Forwarded-Proto": "http"})[0], 401)
        self.assertEqual(self.request("login", {"password": PASSWORD}, **headers)[0], 426)
        self.assertEqual(self.request("password", {"current": PASSWORD, "mode": "basic"},
                                      **headers)[0], 426)
        self.assertEqual(self.request("check", address="127.0.0.1",
                                      **headers)[0], 401)

    def test_https_logout_revokes_both_transport_sessions(self):
        secure_cookie = self.enable()
        _, _, cookie = self.request("login", {"password": PASSWORD}, **{
            "X-Forwarded-Proto": "http", "Origin": "http://printer.local"})
        self.assertEqual(self.request("logout", {}, cookie=secure_cookie + "; " + cookie.split(";")[0])[0], 200)
        self.assertFalse(self.state.sessions)

    def test_missing_or_malformed_https_proxy_configuration_fails_explicitly(self):
        for origin in ("", "https://[invalid"):
            with self.subTest(origin=origin), self.assertLogs(level="ERROR"):
                status, body, _ = self.request("settings", **{
                    "X-Forwarded-Proto": "http", "X-Re3D-HTTPS-Origin": origin})
            self.assertEqual(status, 500)
            self.assertIn("HTTPS redirect", body["error"])

    def lockdown(self):
        with patch.object(auth, "lockdown_readiness", return_value={"ready": True}):
            status, _, _ = self.request("password", {"mode": "lockdown", "new": PASSWORD, "pin": "1234"})
        self.assertEqual(status, 200)

    def test_existing_passwords_migrate_to_basic_without_reset(self):
        record = auth.credential(PASSWORD)
        self.state.path.write_text(json.dumps(record))
        loaded = auth.AuthState(self.state.path)
        self.assertEqual(loaded.mode, "basic")
        self.assertTrue(loaded.verify(PASSWORD))
        self.assertFalse(loaded.has_pin)
        record.update(mode="lockdown", pin=None)
        self.state.path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            auth.AuthState(self.state.path)

    def test_lockdown_requires_pin_and_ssh_password_before_persisting(self):
        self.assertEqual(self.request("password", {"mode": "lockdown", "new": PASSWORD})[0], 400)
        for value in ("123", "abcd", "１２３４"):
            self.assertEqual(self.request("password", {"mode": "lockdown", "new": PASSWORD,
                                                       "pin": value})[0], 400)
        with patch.object(auth, "lockdown_readiness", return_value={"ready": False, "error": "Default pi password"}):
            status, body, _ = self.request("password", {"mode": "lockdown", "new": PASSWORD, "pin": "1234"})
        self.assertEqual(status, 409)
        self.assertIn("pi password", body["error"])
        self.assertIsNone(self.state.record)

    def test_pin_and_admin_sessions_are_separate_and_pin_is_local_only(self):
        self.lockdown()
        self.assertEqual(self.request("check", address="127.0.0.1")[0], 401)
        status, _, admin = self.request("login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("check", cookie=admin, address="127.0.0.1")[0], 401)
        self.assertEqual(self.request("pin", {"pin": "1234"})[0], 403)
        status, _, pin = self.request("pin", {"pin": "1234"}, address="127.0.0.1")
        self.assertEqual(status, 200)
        self.assertIn(auth.PIN_COOKIE, pin)
        self.assertEqual(self.request("check", cookie=pin, address="127.0.0.1")[0], 204)
        self.assertEqual(self.request("check", cookie=pin)[0], 401)
        self.assertEqual(self.request("password", {"mode": "off"}, cookie=pin, address="127.0.0.1")[0], 403)
        self.assertEqual(self.request("password", {"pin": "5678", "mode": "lockdown"},
                                      cookie=pin, address="127.0.0.1")[0], 403)
        self.assertNotIn("1234", self.state.path.read_text())
        self.assertEqual(self.request("lock", {}, cookie=pin, address="127.0.0.1")[0], 200)
        self.assertEqual(self.request("check", cookie=pin, address="127.0.0.1")[0], 401)

    def test_idle_deadline_is_server_enforced_and_background_checks_do_not_refresh_it(self):
        self.lockdown()
        _, _, pin = self.request("pin", {"pin": "1234"}, address="127.0.0.1")
        now = time.monotonic()
        with patch.object(auth.time, "monotonic", return_value=now + 299):
            self.assertEqual(self.request("check", cookie=pin, address="127.0.0.1")[0], 204)
        with patch.object(auth.time, "monotonic", return_value=now + 301):
            self.assertEqual(self.request("check", cookie=pin, address="127.0.0.1")[0], 401)
            self.assertEqual(self.request("activity", {}, cookie=pin, address="127.0.0.1")[0], 401)

    def test_activity_refreshes_pin_but_never_remote_access(self):
        self.lockdown()
        _, _, pin = self.request("pin", {"pin": "1234"}, address="127.0.0.1")
        now = time.monotonic()
        with patch.object(auth.time, "monotonic", return_value=now + 290):
            self.assertEqual(self.request("activity", {}, cookie=pin, address="127.0.0.1")[0], 200)
            self.assertEqual(self.request("activity", {}, cookie=pin)[0], 403)
        with patch.object(auth.time, "monotonic", return_value=now + 301):
            self.assertEqual(self.request("check", cookie=pin, address="127.0.0.1")[0], 204)

    def test_pin_attempt_rate_limit_and_administrator_pin_change(self):
        self.lockdown()
        for _ in range(5):
            self.assertEqual(self.request("pin", {"pin": "9999"}, address="127.0.0.1")[0], 403)
        self.assertEqual(self.request("pin", {"pin": "1234"}, address="127.0.0.1")[0], 429)
        self.assertEqual(self.request("password", {"current": PASSWORD, "mode": "lockdown",
                                                  "pin": "5678"}, address="192.168.1.3")[0], 200)
        self.assertTrue(self.state.verify_pin("5678"))
        self.assertFalse(self.state.verify_pin("1234"))

    def test_status_distinguishes_requested_mode_from_boot_policy(self):
        self.lockdown()
        with patch.object(auth, "APPLIED_MODE", Path(self.directory.name) / "mode"):
            _, status, _ = self.request("status", address="127.0.0.1")
            self.assertEqual(status["mode"], "lockdown")
            self.assertEqual(status["applied_mode"], "off")
            self.assertTrue(status["reboot_required"])
            auth.APPLIED_MODE.write_text("lockdown\n")
            self.assertFalse(self.request("status")[1]["reboot_required"])

    def test_logout_and_expiry_request_connection_revocation_but_anonymous_logout_does_not(self):
        marker = self.state.path.with_name("revoke")
        self.assertEqual(self.request("logout", {})[0], 200)
        self.assertFalse(marker.exists())
        cookie = self.enable()
        self.assertEqual(self.request("logout", {}, cookie=cookie)[0], 200)
        self.assertTrue(marker.exists())
        marker.unlink()
        _, _, cookie = self.request("login", {"password": PASSWORD})
        with patch.object(auth.time, "monotonic", return_value=time.monotonic() + auth.SESSION_SECONDS + 1):
            self.state.prune()
        self.assertTrue(marker.exists())

    def test_basic_and_off_do_not_require_host_policy_reboot(self):
        with patch.object(auth, "APPLIED_MODE", Path(self.directory.name) / "mode"):
            self.enable()
            self.assertFalse(self.request("status")[1]["reboot_required"])


class DeploymentTests(unittest.TestCase):
    def test_build_and_updater_install_authentication(self):
        build = (FILESYSTEM.parent / "start_chroot_script").read_text()
        update = (FILESYSTEM.parents[3] / "tools/re3d-os-update.sh").read_text(encoding="utf-8")
        self.assertIn("bash /opt/mconfig/install_auth.sh image", build)
        self.assertIn("bash /opt/mconfig/install_auth.sh live", update)
        self.assertIn("auth_server.py", update)
        self.assertIn("prepare_auth_tls.sh", update)
        installer = (ASSETS / "install_auth.sh").read_text()
        self.assertNotIn("systemctl restart mconfig", installer)
        self.assertNotIn("openssl req", installer, "image build must not share TLS keys")
        self.assertIn('if [[ "$MODE" == "live" ]]', installer)
        self.assertNotIn("moonraker.conf", installer, "slicer API policy must remain unchanged")

    def test_busybox_private_and_auth_not_in_web_root(self):
        unit = (FILESYSTEM / "root_init/etc/systemd/system/mconfig.service").read_text()
        self.assertIn("127.0.0.1:8085", unit)
        self.assertNotIn("0.0.0.0", unit)
        self.assertFalse((ASSETS / "www/auth_server.py").exists())
        common = (ASSETS / "nginx/common.conf").read_text()
        self.assertIn("proxy_set_header X-Real-IP $remote_addr", common)
        self.assertIn("internal;", common)
        self.assertIn("auth_request /_re3d_auth", common)
        unit = (FILESYSTEM / "root_init/etc/systemd/system/re3d-auth.service").read_text()
        self.assertIn("User=re3d-auth", unit)
        self.assertIn("StateDirectoryMode=0700", unit)


if __name__ == "__main__":
    unittest.main()
