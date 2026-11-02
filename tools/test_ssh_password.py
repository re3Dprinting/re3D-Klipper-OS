import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/modules/fullpageos/filesystem/opt/mconfig"
spec = importlib.util.spec_from_file_location("ssh_password", ASSETS / "ssh_password.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class PasswordTests(unittest.TestCase):
    def test_current_password_is_verified_and_only_pi_is_changed(self):
        record = ["pi", "$hash", "20000", "0", "99999", "7", "", "", ""]
        with patch.object(helper, "shadow_record", return_value=record), \
                patch.object(helper, "matches", return_value=True) as matches, \
                patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(helper.change_password({"old": "raspberry", "new": "new pass:word"})[0], 200)
        matches.assert_called_once_with("raspberry", "$hash")
        run.assert_called_once_with(["chpasswd"], input="pi:new pass:word\n", text=True,
                                    capture_output=True, timeout=15)

    def test_wrong_password_and_locked_accounts_never_invoke_chpasswd(self):
        for hashed in ("!locked", "*", "", "$valid"):
            with patch.object(helper, "shadow_record", return_value=["pi", hashed]), \
                    patch.object(helper, "matches", return_value=False), \
                    patch.object(helper.subprocess, "run") as run:
                self.assertEqual(helper.change_password({"old": "wrong", "new": "new pass"})[0], 403)
                run.assert_not_called()

    def test_rejects_short_default_unchanged_and_injected_passwords(self):
        for new in ("1234567", "raspberry", "same password", "password\nroot:injection", "password\0"):
            with patch.object(helper.subprocess, "run") as run:
                self.assertEqual(helper.change_password({"old": "same password", "new": new})[0], 400)
                run.assert_not_called()
        self.assertEqual(helper.change_password({"old": "old", "new": "password", "user": "root"})[0], 400)

    def test_system_rejection_is_reported_without_password_output(self):
        with patch.object(helper, "shadow_record", return_value=["pi", "$hash"]), \
                patch.object(helper, "matches", return_value=True), \
                patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 1, "", "chpasswd: pam_chauthtok() failed: Authentication token manipulation error\n"
                    "old password $hash")), \
                self.assertLogs(level="ERROR") as logs:
            status, body = helper.change_password({"old": "old", "new": "password"})
        self.assertEqual(status, 500)
        self.assertIn("service logs", body["error"])
        message = " ".join(logs.output)
        self.assertIn("Authentication token manipulation error", message)
        for secret in ("old", "password", "$hash"):
            self.assertNotIn(secret, message.split(": ", 1)[-1])
        self.assertNotIn("\n", message)

    def test_missing_system_error_details_are_explicit(self):
        with patch.object(helper, "shadow_record", return_value=["pi", "$hash"]), \
                patch.object(helper, "matches", return_value=True), \
                patch.object(helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
                self.assertLogs(level="ERROR") as logs:
            self.assertEqual(helper.change_password({"old": "old", "new": "password"})[0], 500)
        self.assertIn("chpasswd supplied no error details", logs.output[0])

    def test_readiness_rejects_default_locked_and_expired_accounts(self):
        for hashed, expired, default in (("!locked", "", False), ("", "", False),
                                          ("$hash", "1", False), ("$hash", "", True),
                                          ("$hash", "", False)):
            record = ["pi", hashed, "20000", "0", "99999", "7", "", expired, ""]
            with patch.object(helper, "shadow_record", return_value=record), \
                    patch.object(helper, "matches", return_value=default):
                self.assertEqual(helper.password_ready(), bool(hashed == "$hash" and not expired and not default))

    def test_deployment_keeps_root_helper_private_and_available(self):
        unit = (ROOT / "src/modules/fullpageos/filesystem/root_init/etc/systemd/system/re3d-ssh-password.service").read_text()
        self.assertIn("Group=re3d-auth", unit)
        self.assertIn("RuntimeDirectoryMode=0750", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertIn("ssh_password.py", (ROOT / "tools/re3d-os-update.sh").read_text())


if __name__ == "__main__":
    unittest.main()
