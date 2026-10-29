import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


CGI = (Path(__file__).resolve().parents[1] / "src/modules/fullpageos/filesystem"
       / "opt/mconfig/www/cgi-bin")
spec = importlib.util.spec_from_file_location("system_health", CGI / "system_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class SystemHealthTests(unittest.TestCase):
    def test_temperature_thresholds(self):
        for value, status in [(74999, "ok"), (75000, "warning"), (84999, "warning"), (85000, "error")]:
            with self.subTest(value=value), patch.object(health, "read_text", return_value=str(value)):
                self.assertEqual(health.temperature()[0], status)
        with patch.object(health, "read_text", return_value="nan"):
            self.assertRaises(ValueError, health.temperature)

    def test_load_compared_to_core_count(self):
        for load, status in [(3.99, "ok"), (4, "warning")]:
            with patch.object(health, "read_text", return_value=f"{load} 2 1 1/100 1"), \
                    patch.object(health.os, "cpu_count", return_value=4):
                result = health.cpu_load()
                self.assertEqual(result[0], status)
                self.assertIn("1 / 5 / 15 min; 4 cores", result[1])

    def test_memory_and_storage_thresholds(self):
        for percent, status in [(84, "ok"), (85, "warning"), (94, "warning"), (95, "error")]:
            with self.subTest(percent=percent):
                self.assertEqual(health.usage_status(percent), status)
                with patch.object(health, "read_text", return_value=(
                        f"MemTotal: 100 kB\nMemAvailable: {100 - percent} kB")):
                    self.assertEqual(health.memory()[0], status)
                usage = health.shutil._ntuple_diskusage(100, percent, 100 - percent)
                with patch.object(health.shutil, "disk_usage", return_value=usage):
                    self.assertEqual(health.disk("/")[0], status)
        with patch.object(health, "read_text", return_value="MemTotal: 0 kB\nMemAvailable: 0 kB"):
            self.assertRaises(ValueError, health.memory)

    def test_uptime(self):
        with patch.object(health, "read_text", return_value="90061.0 0"):
            self.assertEqual(health.uptime(), ("ok", "1d 1h 1m"))

    def test_power_flags_distinguish_current_and_historical(self):
        for flags, status, text in [
                ("0x0", "ok", "No undervoltage"),
                ("0x1", "error", "Now: Undervoltage"),
                ("0x50000", "warning", "Since boot: Undervoltage, Throttling"),
                ("0xf000f", "error", "Soft temperature limit"),
                ("0x100", "warning", "Unrecognized flags")]:
            with self.subTest(flags=flags), \
                    patch.object(health.shutil, "which", return_value="/usr/bin/vcgencmd"), \
                    patch.object(health, "run_command", return_value=f"throttled={flags}"):
                result = health.throttling()
                self.assertEqual(result[0], status)
                self.assertIn(text, result[1])
        with patch.object(health.shutil, "which", return_value=None), \
                patch.object(health.Path, "is_file", return_value=False):
            self.assertRaisesRegex(ValueError, "not installed", health.throttling)

    def test_service_states(self):
        for loaded, active, sub, status in [
                ("loaded", "active", "running", "ok"),
                ("loaded", "failed", "failed", "error"),
                ("loaded", "inactive", "dead", "error"),
                ("loaded", "activating", "start", "warning"),
                ("not-found", "inactive", "dead", "error")]:
            output = f"LoadState={loaded}\nActiveState={active}\nSubState={sub}"
            with self.subTest(output=output), patch.object(health, "run_command", return_value=output):
                self.assertEqual(health.service("klipper")[0], status)

    def test_unavailable_checks_are_explicit_and_do_not_hide_other_results(self):
        for failure in [PermissionError("denied"), FileNotFoundError("missing"),
                        subprocess.TimeoutExpired("systemctl", 3), ValueError("invalid")]:
            with self.subTest(failure=failure), \
                    patch.object(health, "temperature", side_effect=failure), \
                    patch.object(health, "cpu_load", return_value=("ok", "load")):
                data = health.collect_health()
                self.assertEqual(len(data["checks"]), 9)
                self.assertEqual(data["checks"][0]["status"], "unknown")
                self.assertTrue(data["checks"][0]["detail"].startswith("Unavailable:"))
                self.assertEqual(data["checks"][1]["status"], "ok")
                self.assertEqual(json.loads(json.dumps(data)), data)

    def test_commands_are_bounded_and_failures_are_reported(self):
        with patch.object(health.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["vcgencmd"], 1, "", "permission denied")) as run:
            self.assertRaisesRegex(ValueError, "permission denied", health.run_command, ["vcgencmd"])
            self.assertEqual(run.call_args.kwargs["timeout"], 3)

    def test_cgi_headers_and_json(self):
        result = subprocess.run(
            [sys.executable, "-B", str(CGI / "system_health.py")],
            capture_output=True, text=True, check=True)
        headers, body = result.stdout.split("\n\n", 1)
        self.assertIn("Content-Type: application/json", headers)
        self.assertIn("Cache-Control: no-store", headers)
        self.assertEqual(len(json.loads(body)["checks"]), 9)


if __name__ == "__main__":
    unittest.main()
