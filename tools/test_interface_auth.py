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
        for value in ("short", 123, ["password"]):
            self.assertEqual(self.request("password", {"new": value})[0], 400)
        self.assertEqual(self.request("password", {"new": "x" * 1025})[0], 400)
        self.assertIsNone(self.state.record)

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
