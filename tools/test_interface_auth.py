import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


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
        self.assertIn(b"--brand: #E7C547", css)
        self.assertNotIn(PASSWORD.encode(), css)
        status, logo, _ = self.request("logo.png")
        self.assertEqual(status, 200)
        self.assertTrue(logo.startswith(b"\x89PNG\r\n\x1a\n"))

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

    def test_lockdown_requires_pin_and_ssh_key_before_persisting(self):
        self.assertEqual(self.request("password", {"mode": "lockdown", "new": PASSWORD})[0], 400)
        for value in ("123", "abcd", "１２３４"):
            self.assertEqual(self.request("password", {"mode": "lockdown", "new": PASSWORD,
                                                       "pin": value})[0], 400)
        with patch.object(auth, "lockdown_readiness", return_value={"ready": False, "error": "No SSH key"}):
            status, body, _ = self.request("password", {"mode": "lockdown", "new": PASSWORD, "pin": "1234"})
        self.assertEqual(status, 409)
        self.assertIn("SSH key", body["error"])
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
