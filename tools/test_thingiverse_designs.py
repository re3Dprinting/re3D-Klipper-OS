import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM = ROOT / "src/modules/fullpageos/filesystem"
spec = importlib.util.spec_from_file_location(
    "thingiverse_cache", FILESYSTEM / "opt/mconfig/www/cgi-bin/thingiverse_designs.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)

DESIGN = {"title": "A printed vase", "image": "https://cdn.thingiverse.com/assets/vase.png"}


class DesignCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.file = root / "news/thingiverse.json"
        self.airgap = root / "airgap"
        self.applied = root / "applied"
        self.lockdown = root / "lockdown"
        for name, value in (("CACHE", self.file), ("AIRGAP", (self.airgap, self.applied)),
                            ("LOCKDOWN", self.lockdown)):
            patcher = patch.object(cache, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method="GET", payload=None, raw=None, address="127.0.0.1", content_type="application/json"):
        body = raw if raw is not None else json.dumps(payload) if payload is not None else ""
        output = io.StringIO()
        with patch.dict(os.environ, {"REQUEST_METHOD": method, "REMOTE_ADDR": address,
                                    "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(len(body))}):
            with patch("sys.stdin", io.StringIO(body)), contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(io.StringIO()):
                    cache.main()
        headers, body = output.getvalue().split("\n\n", 1)
        return headers, json.loads(body)

    def test_initial_cache_is_explicitly_empty_without_creating_files(self):
        headers, data = self.request(address="192.168.1.10")
        self.assertIn("200 OK", headers)
        self.assertEqual(data["designs"], [])
        self.assertIsNone(data["updatedAt"])
        self.assertFalse(self.file.exists())

    def test_local_collection_is_atomic_and_readable_remotely(self):
        designs = [dict(DESIGN, title=f"Print {index}") for index in range(6)]
        headers, saved = self.request("POST", {"designs": designs})
        self.assertIn("200 OK", headers)
        self.assertEqual(saved["designs"], designs)
        self.assertIsNotNone(saved["updatedAt"])
        self.assertEqual(saved, self.request(address="192.168.1.10")[1])
        self.assertEqual(list(self.file.parent.iterdir()), [self.file])

    def test_failed_checks_retain_last_successful_designs_and_update_shared_status(self):
        first = self.request("POST", {"designs": [DESIGN]})[1]
        headers, failed = self.request("POST", {"error": "Thingiverse verification page"})
        self.assertIn("200 OK", headers)
        self.assertEqual(first["designs"], failed["designs"])
        self.assertEqual(first["updatedAt"], failed["updatedAt"])
        self.assertEqual(failed["lastError"], "Thingiverse verification page")
        self.assertIsNotNone(failed["checkedAt"])
        self.assertIsNone(self.request("POST", {"designs": [DESIGN]})[1]["lastError"])

    def test_remote_writes_wrong_content_types_and_policy_blocked_writes_do_not_change_cache(self):
        self.request("POST", {"designs": [DESIGN]})
        saved = self.file.read_bytes()
        self.assertIn("403 Forbidden", self.request("POST", {"designs": [DESIGN]}, address="192.168.1.10")[0])
        self.assertIn("415", self.request("POST", {"designs": [DESIGN]}, content_type="text/plain")[0])
        for marker in (self.airgap, self.applied, self.lockdown):
            marker.touch()
            self.assertIn("403 Forbidden", self.request("POST", {"designs": [DESIGN]})[0])
            self.assertEqual(self.request()[1]["designs"], [DESIGN])
            marker.unlink()
        self.assertEqual(self.file.read_bytes(), saved)

    def test_invalid_design_shapes_and_unsafe_images_preserve_previous_metadata(self):
        self.request("POST", {"designs": [DESIGN]})
        saved = self.file.read_bytes()
        invalid = [
            {"designs": []}, {"designs": [DESIGN] * 7}, {"designs": [{"title": ""}]},
            {"designs": [dict(DESIGN, title="x" * 201)]},
            {"designs": [dict(DESIGN, image="javascript:alert(1)")]},
            {"designs": [dict(DESIGN, image="https://cdn.thingiverse.com.evil.test/image.png")]},
            {"designs": [dict(DESIGN, image="https://cdn.thingiverse.com:8081/image.png")]},
            {"designs": [dict(DESIGN, image="https://resize.thingiverse.com/?url=https://evil.test/image.png")]},
            {"error": ""}, {"error": "x" * 301}, {"error": "bad", "designs": [DESIGN]}, []
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertIn("400 Bad Request", self.request("POST", payload)[0])
                self.assertEqual(self.file.read_bytes(), saved)
        for raw in ("not json", "", "x" * (cache.MAX_BYTES + 1)):
            self.assertIn("400 Bad Request", self.request("POST", raw=raw)[0])
        self.assertIn("405", self.request("DELETE")[0])

    def test_thumbnail_resize_urls_and_unicode_titles_are_preserved(self):
        design = {
            "title": "  Vase \u2013   huge print  ",
            "image": "https://resize.thingiverse.com/?url=https%3A%2F%2Fcdn.thingiverse.com%2Fvase.webp&w=292&h=219"
        }
        headers, data = self.request("POST", {"designs": [design]})
        self.assertIn("200 OK", headers)
        self.assertEqual(data["designs"][0]["title"], "Vase \u2013 huge print")
        self.assertEqual(data["designs"][0]["image"], design["image"])

    def test_corrupt_cache_and_io_failures_are_reported_not_replaced_with_empty_success(self):
        self.file.parent.mkdir()
        self.file.write_text('{"designs":[{}]}')
        self.assertIn("500 Internal Server Error", self.request()[0])
        self.assertEqual(self.file.read_text(), '{"designs":[{}]}')
        self.file.unlink()
        with patch.object(cache, "save_cache", side_effect=PermissionError("denied")):
            self.assertIn("500 Internal Server Error", self.request("POST", {"designs": [DESIGN]})[0])

    def test_proxy_keeps_authentication_and_limits_mutations_to_loopback(self):
        nginx = (FILESYSTEM / "opt/mconfig/nginx/configurator.conf").read_text()
        route = nginx.split("location = /cgi-bin/thingiverse_designs.py {", 1)[1].split("    location / {", 1)[0]
        self.assertIn("limit_except GET", route)
        self.assertIn("allow 127.0.0.1;", route)
        self.assertIn("allow ::1;", route)
        self.assertIn("deny all;", route)
        self.assertNotIn("auth_request off", route)
        for source in (ROOT / "src/modules/fullpageos/start_chroot_script", ROOT / "tools/re3d-os-update.sh"):
            self.assertIn("cgi-bin/thingiverse_designs.py", source.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
