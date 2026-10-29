"""Optional real-nginx checks: set RE3D_TEST_NGINX to the nginx executable."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/modules/fullpageos/filesystem/opt/mconfig"
spec = importlib.util.spec_from_file_location("nginx_auth", ASSETS / "auth_server.py")
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (f"<html><body>backend {self.path}</body></html>" if self.path == "/"
                else "backend " + self.path).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/" else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.environ.get("RE3D_TEST_NGINX") and shutil.which("openssl"),
                     "Set RE3D_TEST_NGINX and provide openssl for proxy integration tests")
class NginxAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="re3d-nginx-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.work = Path(cls.directory.name)
        cls.auth = auth.ThreadingHTTPServer(("127.0.0.1", 0), auth.AuthHandler)
        cls.auth.state = auth.AuthState(cls.work / "password.json")
        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        for server in (cls.auth, cls.backend):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.addClassCleanup(cls.stop_server, server, thread)
        ports = []
        sockets = []
        for _ in range(4):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
            ports.append(sock.getsockname()[1])
        for sock in sockets:
            sock.close()
        cls.http, cls.https, cls.config_http, cls.config_https = ports
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-days", "1", "-subj", "/CN=localhost",
                        "-keyout", str(cls.work / "tls.key"),
                        "-out", str(cls.work / "tls.crt")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        (cls.work / "logs").mkdir()
        (cls.work / "temp").mkdir()
        (cls.work / "html").mkdir()
        (cls.work / "html/index.html").write_text("<html><body>printer interface</body></html>")

        def adapt(text):
            return (text.replace("/etc/nginx/re3d-auth/", cls.work.as_posix() + "/")
                    .replace("/etc/re3d-auth/", cls.work.as_posix() + "/")
                    .replace("127.0.0.1:8090", f"127.0.0.1:{cls.auth.server_port}")
                    .replace("127.0.0.1:8085", f"127.0.0.1:{cls.backend.server_port}")
                    .replace("listen 443 ssl", f"listen {cls.https} ssl")
                    .replace("listen 8081;", f"listen {cls.config_http};")
                    .replace("listen 8443 ssl", f"listen {cls.config_https} ssl")
                    .replace("$re3d_tls_port 443;", f"$re3d_tls_port {cls.https};")
                    .replace("$re3d_tls_port 8443;", f"$re3d_tls_port {cls.config_https};")
                    .replace("default 8081;", f"default {cls.config_http};")
                    .replace("https 8443;", f"https {cls.config_https};"))

        for name in ("common", "mainsail", "configurator", "maps"):
            (cls.work / f"{name}.conf").write_text(adapt((ASSETS / f"nginx/{name}.conf").read_text()))
        build = (ROOT / "src/modules/fullpageos/start_chroot_script").read_text()
        main = re.search(r"cat >/etc/nginx/sites-available/mainsail <<EOF\n(.*?)\nEOF",
                         build, re.S)[1].replace("\\$", "$")
        main = main.replace("listen 80 default_server;", f"listen {cls.http} default_server;\n"
                            f"include {cls.work.as_posix()}/mainsail.conf;")
        main = re.sub(r"root /home/.*?/mainsail;", f"root {cls.work.as_posix()}/html;", main)
        main = main.replace("/var/log/nginx/", cls.work.as_posix() + "/logs/")
        upstreams = "\n".join(f"upstream {name} {{ server 127.0.0.1:{cls.backend.server_port}; }}"
                              for name in ("apiserver", "mjpgstreamer1", "mjpgstreamer2",
                                           "mjpgstreamer3", "mjpgstreamer4"))
        config = (f"daemon off;\nmaster_process on;\npid {cls.work.as_posix()}/nginx.pid;\n"
                  "events {}\nhttp {\ntypes { text/html html; }\n" + upstreams +
                  "\nmap $http_upgrade $connection_upgrade { default upgrade; '' close; }\n" +
                  (cls.work / "maps.conf").read_text() + main +
                  "\n" + (cls.work / "configurator.conf").read_text() + "\n}\n")
        (cls.work / "nginx.conf").write_text(config)
        command = [os.environ["RE3D_TEST_NGINX"], "-p", str(cls.work) + os.sep,
                   "-c", str(cls.work / "nginx.conf")]
        check = subprocess.run(command + ["-t"], capture_output=True, text=True)
        if check.returncode:
            raise RuntimeError(check.stderr)
        cls.log = (cls.work / "nginx-output.log").open("w")
        cls.addClassCleanup(cls.log.close)
        cls.process = subprocess.Popen(command, stdout=cls.log, stderr=cls.log)
        cls.addClassCleanup(cls.stop_nginx, command)
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError((cls.work / "nginx-output.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", cls.http), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError("nginx did not start")

    @staticmethod
    def stop_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join()

    @classmethod
    def stop_nginx(cls, command):
        if cls.process.poll() is None:
            subprocess.run(command + ["-s", "quit"], capture_output=True, timeout=5)
            cls.process.wait(timeout=10)

    def setUp(self):
        with self.auth.state.lock:
            self.auth.state.save(None)
            self.auth.state.attempts.clear()

    def request(self, port, path="/", data=None, cookie="", local=False, **headers):
        use_tls = port in (self.https, self.config_https)
        options = {"timeout": 5, "source_address": ("127.0.0.1" if local else "127.0.0.2", 0)}
        if use_tls:
            options["context"] = ssl._create_unverified_context()
        constructor = http.client.HTTPSConnection if use_tls else http.client.HTTPConnection
        connection = constructor("127.0.0.1", port, **options)
        self.addCleanup(connection.close)
        values = {"Host": f"localhost:{port}", "Cookie": cookie,
                  "Origin": f'{"https" if use_tls else "http"}://localhost:{port}',
                  "Content-Type": "application/json"}
        values.update(headers)
        connection.request("POST" if data is not None else "GET", path,
                           json.dumps(data) if data is not None else None, values)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def enable(self):
        response = self.request(self.https, "/auth/password", {"new": "long test password"})
        self.assertEqual(response[0], 200, response)
        return response[2]["Set-Cookie"].split(";")[0]

    def test_off_does_not_force_either_interface_to_https(self):
        for port in (self.http, self.config_http):
            status, _, headers = self.request(port)
            self.assertEqual(status, 200)
            self.assertNotIn("Location", headers)
            self.assertEqual(self.request(port, "/auth/kiosk-client.js")[0], 200)

    def test_protected_modes_require_https_but_keep_kiosk_http(self):
        self.enable()
        for port, target in ((self.http, self.https), (self.config_http, self.config_https)):
            response = self.request(port)
            self.assertEqual(response[0], 307)
            self.assertEqual(response[2]["Location"], f"https://localhost:{target}/")
            self.assertEqual(response[2]["Cache-Control"], "no-store")
            self.assertEqual(self.request(port, local=True)[0], 200)

    def test_turning_auth_off_stops_https_redirects_without_reloading_nginx(self):
        self.enable()
        self.assertEqual(self.request(self.http)[0], 307)
        self.assertEqual(self.request(self.https, "/auth/password",
                                      {"current": "long test password", "disable": True})[0], 200)
        self.assertEqual(self.request(self.http)[0], 200)
        self.assertEqual(self.request(self.config_http)[0], 200)

    def test_security_forms_use_https_even_when_off_and_do_not_trust_forwarded_client_headers(self):
        for port, target in ((self.http, self.https), (self.config_http, self.config_https)):
            for path in ("/auth/settings", "/auth/login", "/auth/settings?return=/"):
                status, _, headers = self.request(port, path, **{
                    "X-Forwarded-Proto": "https", "X-Re3D-HTTPS-Origin": "https://attacker.example"})
                self.assertEqual(status, 307)
                self.assertEqual(headers["Location"], f"https://localhost:{target}{path}")
            self.assertEqual(self.request(port, "/auth/password", {"new": "password"})[0], 426)
        self.assertIsNone(self.auth.state.record)

    def test_both_interfaces_and_proxy_paths_require_password(self):
        cookie = self.enable()
        for port, paths in ((self.https, ("/", "/index.html", "/server/info", "/websocket",
                                         "/webcam/", "/webcam2/", "/webcam3/", "/webcam4/")),
                            (self.config_https, ("/", "/cgi-bin/set_display_mode.sh"))):
            for path in paths:
                with self.subTest(port=port, path=path):
                    status, _, headers = self.request(port, path, **{"X-Real-IP": "127.0.0.1"})
                    self.assertEqual(status, 302)
                    self.assertEqual(headers["Location"], f"https://localhost:{port}/auth/login")
                    self.assertEqual(self.request(port, path, cookie=cookie)[0], 200)
                    self.assertEqual(self.request(port, path, local=True)[0], 200)
        self.assertEqual(self.request(self.https, "/_re3d_auth")[0], 404)

    def test_login_change_disable_and_local_management_through_nginx(self):
        self.enable()
        self.assertEqual(self.request(self.http, "/auth/password", {"disable": True}, local=True)[0], 403)
        login = self.request(self.https, "/auth/login", {"password": "long test password"})
        self.assertEqual(login[0], 200)
        cookie = login[2]["Set-Cookie"].split(";")[0]
        self.assertIn("Secure", login[2]["Set-Cookie"])
        change = self.request(self.config_https, "/auth/password",
                              {"current": "long test password", "new": "a different password"},
                              cookie=cookie)
        self.assertEqual(change[0], 200)
        self.assertEqual(self.request(self.https, cookie=cookie)[0], 302)
        self.assertEqual(self.request(self.http, "/auth/password",
                                      {"current": "a different password", "disable": True},
                                      local=True)[0], 200)
        self.assertEqual(self.request(self.https)[0], 200)

    def test_preserving_scheme_and_host_when_opening_configurator(self):
        for port, target, scheme in ((self.https, self.config_https, "https"),
                                     (self.http, self.config_http, "http")):
            response = self.request(port, "/configurator/", local=True)
            self.assertEqual(response[0], 302)
            self.assertEqual(response[2]["Location"], f"{scheme}://localhost:{target}/")

    def test_auth_failure_does_not_open_interfaces(self):
        self.enable()
        self.assertEqual(self.request(self.https, cookie="re3d_session=not-a-session")[0], 302)
        with patch.object(auth.AuthHandler, "do_GET",
                          lambda handler: handler.reply(500, {"error": "Service failure"})):
            self.assertEqual(self.request(self.https)[0], 500)
            self.assertEqual(self.request(self.http)[0], 500)

    def test_lockdown_removes_local_bypass_and_pin_cannot_authenticate_remotely(self):
        with patch.object(auth, "lockdown_readiness", return_value={"ready": True}):
            enabled = self.request(self.https, "/auth/password",
                                   {"new": "admin test password", "mode": "lockdown", "pin": "1234"})
        self.assertEqual(enabled[0], 200)
        admin = enabled[2]["Set-Cookie"].split(";")[0]
        for port, target in ((self.http, self.https), (self.config_http, self.config_https)):
            for path in ("/", "/auth/status"):
                status, _, headers = self.request(port, path, cookie=admin, **{
                    "X-Forwarded-Proto": "https", "X-Real-IP": "127.0.0.1"})
                self.assertEqual(status, 307)
                self.assertEqual(headers["Location"], f"https://localhost:{target}{path}")
        self.assertEqual(self.request(self.http, local=True)[0], 302)
        self.assertEqual(self.request(self.http, cookie=admin, local=True)[0], 302)
        self.assertEqual(self.request(self.https, cookie=admin)[0], 200)
        self.assertEqual(self.request(self.https, "/auth/pin", {"pin": "1234"})[0], 403)
        pin = self.request(self.http, "/auth/pin", {"pin": "1234"}, local=True)
        self.assertEqual(pin[0], 200)
        token = pin[2]["Set-Cookie"].split(";")[0]
        for port in (self.http, self.config_http):
            status, body, _ = self.request(port, cookie=token, local=True)
            self.assertEqual(status, 200)
            self.assertIn(b'/auth/kiosk-client.js', body)
        self.assertEqual(self.request(self.https, cookie=token)[0], 302)
        self.assertEqual(self.request(self.http, "/auth/lock", {}, cookie=token, local=True)[0], 200)
        self.assertEqual(self.request(self.http, cookie=token, local=True)[0], 302)


if __name__ == "__main__":
    unittest.main()
