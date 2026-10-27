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
CGI = FILESYSTEM / "opt/mconfig/www/cgi-bin"
spec = importlib.util.spec_from_file_location("setup_cgi", CGI / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.state = root / "state/step"
        self.flash = root / "firstboot-splash"
        for name, value in [("STATE", self.state), ("FLASH_FLAG", self.flash)]:
            patcher = patch.object(setup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method="GET", payload=None, raw=None):
        body = raw if raw is not None else json.dumps(payload) if payload else ""
        output = io.StringIO()
        with patch.dict(os.environ, {"REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(body))}):
            with patch("sys.stdin", io.StringIO(body)), contextlib.redirect_stdout(output):
                with contextlib.redirect_stderr(io.StringIO()):
                    setup.main()
        headers, body = output.getvalue().split("\n\n", 1)
        return headers, json.loads(body)

    def test_existing_install_get_does_not_opt_in(self):
        headers, data = self.request()
        self.assertIn("200 OK", headers)
        self.assertEqual(data, {"step": None, "flashing": False})
        self.assertFalse(self.state.exists())

    def test_progress_and_completion_persist_atomically(self):
        for step in setup.STEPS:
            with self.subTest(step=step):
                headers, data = self.request("POST", {"step": step})
                self.assertIn("200 OK", headers)
                self.assertEqual(data["step"], step)
                self.assertEqual(self.request()[1]["step"], step)
                self.assertEqual(list(self.state.parent.iterdir()), [self.state])

    def test_invalid_requests_preserve_progress(self):
        setup.save_step("network")
        for raw in ['{"step":"../completed"}', '[]', '{}', 'not json', '']:
            with self.subTest(raw=raw):
                headers, data = self.request("POST", raw=raw)
                self.assertIn("400 Bad Request", headers)
                self.assertIn("error", data)
                self.assertEqual(setup.read_step(), "network")
        self.assertIn("405 Method Not Allowed", self.request("DELETE")[0])

    def test_legacy_welcome_progress_resumes_at_machine(self):
        setup.save_step("welcome")
        headers, data = self.request()
        self.assertIn("200 OK", headers)
        self.assertEqual(data["step"], "machine")
        self.request("POST", {"step": "machine"})
        self.assertEqual(self.state.read_text().strip(), "machine")

    def test_flashing_blocks_progress_mutations(self):
        self.flash.touch()
        self.assertTrue(self.request()[1]["flashing"])
        self.assertIn("409 Conflict", self.request("POST", {"step": "completed"})[0])
        self.assertFalse(self.state.exists())

    def test_corrupt_state_and_io_errors_are_explicit(self):
        setup.save_step("network")
        self.state.write_text("invalid\n")
        self.assertIn("400 Bad Request", self.request()[0])
        with patch.object(setup, "read_step", side_effect=PermissionError("denied")):
            headers, data = self.request()
        self.assertIn("500 Internal Server Error", headers)
        self.assertIn("error", data)

    def test_only_image_build_initializes_setup(self):
        build = (ROOT / "src/modules/fullpageos/start_chroot_script").read_text(encoding="utf-8")
        updater = (ROOT / "tools/re3d-os-update.sh").read_text(encoding="utf-8")
        self.assertIn("printf 'machine\\n' > /var/lib/re3d-setup/step", build)
        self.assertNotIn("> /var/lib/re3d-setup/step", updater)
        for script in [build, updater]:
            self.assertIn("chmod 0755", script)
            self.assertIn("cgi-bin/setup.py", script)
        self.assertIn("scripts/get_url:/opt/custompios/scripts/get_url", updater)


if __name__ == "__main__":
    unittest.main()
