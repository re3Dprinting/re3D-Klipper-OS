import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM = ROOT / "src/modules/fullpageos/filesystem"
ASSETS = FILESYSTEM / "opt/mconfig"
spec = importlib.util.spec_from_file_location("security_policy", ASSETS / "security_policy.py")
policy = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(ASSETS), *sys.path]):
    spec.loader.exec_module(policy)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("RUNTIME", "MARKER", "SSH_CONFIG", "SSH_UNIT", "SSH_RUNTIME", "SERVICE_BACKUP"):
            mock = patch.object(policy, name, self.root / name)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(policy, "CHROME_POLICIES", (self.root / "chrome/policies/managed/policy.json",))
        mock.start()
        self.addCleanup(mock.stop)

    def test_ipv4_and_ipv6_default_deny_and_only_intended_ports(self):
        rules = policy.firewall_rules()
        self.assertIn("table inet re3d_lockdown", rules)
        self.assertIn("policy drop", rules)
        self.assertIn('iifname "lo" accept', rules)
        self.assertIn("tcp dport { 22, 80, 443, 8081, 8443 } accept", rules)
        for port in ("7125", "8080", "8085", "8090", "5900"):
            self.assertNotIn(port, rules)
        self.assertNotIn("tcp dport", policy.firewall_rules(quarantine=True))
        self.assertTrue(policy.firewall_rules(replace=True).startswith("delete table"))

    def test_missing_ssh_key_keeps_quarantine_and_does_not_mark_applied(self):
        with patch.object(policy, "set_firewall") as firewall, \
                patch.object(policy, "ssh_readiness", return_value={"ready": False, "error": "No key"}):
            with self.assertRaisesRegex(RuntimeError, "No key"):
                policy.apply("lockdown")
        firewall.assert_called_once_with(quarantine=True)
        self.assertFalse((policy.RUNTIME / "mode").exists())

    def test_effective_ssh_override_cannot_silently_leave_password_access(self):
        with patch.object(policy, "set_firewall") as firewall, \
                patch.object(policy, "ssh_readiness", return_value={"ready": True}), \
                patch.object(policy, "run", return_value=subprocess.CompletedProcess([], 0, "passwordauthentication yes\n", "")):
            with self.assertRaisesRegex(RuntimeError, "SSH override"):
                policy.apply("lockdown")
        firewall.assert_called_once_with(quarantine=True)

    def test_lockdown_sets_kiosk_policy_and_activated_marker_after_prerequisites(self):
        effective = ("\n".join(("passwordauthentication no", "kbdinteractiveauthentication no",
                                "authenticationmethods publickey", "pubkeyauthentication yes",
                                "permitrootlogin no", "disableforwarding yes", "allowusers pi",
                                "authorizedkeysfile /home/pi/.ssh/authorized_keys")) + "\n")
        def result(*args, **kwargs):
            output = (f"argv[]=/usr/sbin/sshd -D -f {policy.SSH_CONFIG} ;"
                      if args[:2] == ("systemctl", "show") else effective)
            return subprocess.CompletedProcess(args, 0, output, "")
        with patch.object(policy, "set_firewall") as firewall, \
                patch.object(policy, "ssh_readiness", return_value={"ready": True}), \
                patch.object(policy, "stop_desktops") as desktops, \
                patch.object(policy, "run", side_effect=result):
            policy.apply("lockdown")
        self.assertEqual(firewall.call_count, 2)
        desktops.assert_called_once()
        self.assertEqual((policy.RUNTIME / "mode").read_text().strip(), "lockdown")
        self.assertTrue(policy.MARKER.exists())
        settings = json.loads(policy.CHROME_POLICIES[0].read_text())
        self.assertEqual(settings["DeveloperToolsAvailability"], 2)
        self.assertEqual(settings["URLBlocklist"], ["*"])
        self.assertEqual(settings["IncognitoModeAvailability"], 1)
        if os.name != "nt":
            self.assertEqual(policy.CHROME_POLICIES[0].stat().st_mode & 0o777, 0o644)

    def test_leaving_lockdown_removes_only_owned_policy(self):
        policy.write(policy.SSH_CONFIG, policy.SSH_OPTIONS)
        policy.write(policy.MARKER, "lockdown")
        policy.write(policy.CHROME_POLICIES[0], "{}")
        unrelated = self.root / "other-policy.json"
        unrelated.write_text("preserve")
        with patch.object(policy, "table_exists", return_value=True), \
                patch.object(policy, "run") as run, patch.object(policy, "restore_desktops") as restore:
            policy.apply("basic")
        run.assert_any_call("nft", "delete", "table", "inet", "re3d_lockdown")
        restore.assert_called_once()
        self.assertFalse(policy.SSH_CONFIG.exists())
        self.assertFalse(policy.MARKER.exists())
        self.assertFalse(policy.CHROME_POLICIES[0].exists())
        self.assertEqual(unrelated.read_text(), "preserve")

    def test_firewall_inspection_errors_are_not_treated_as_missing_rules(self):
        result = subprocess.CompletedProcess([], 1, "", "Operation not permitted")
        with patch.object(policy, "run", return_value=result):
            self.assertRaises(RuntimeError, policy.table_exists)

    def test_readiness_requires_enabled_ssh_key_and_safe_permissions(self):
        def result(*args, **kwargs):
            if args == ("id", "-u", "pi"):
                output = "1000"
            elif args == ("systemctl", "is-enabled", "ssh.service"):
                output = "enabled"
            elif args[:1] == ("ssh-keygen",):
                output = "256 SHA256:test recovery key"
            else:
                output = ""
            return subprocess.CompletedProcess(args, 0, output, "")
        with patch.object(policy.shutil, "which", return_value="tool"), \
                patch.object(policy, "run", side_effect=result), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1000, st_mode=0o600)):
            self.assertTrue(policy.ssh_readiness()["ready"])
        with patch.object(policy.shutil, "which", return_value="tool"), \
                patch.object(policy, "run", side_effect=result), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1000, st_mode=0o666)):
            self.assertFalse(policy.ssh_readiness()["ready"])
        with patch.object(policy.shutil, "which", return_value="tool"), \
                patch.object(policy, "run", side_effect=result), \
                patch.object(Path, "stat", side_effect=FileNotFoundError("missing key")):
            self.assertFalse(policy.ssh_readiness()["ready"])

    def test_deployment_and_display_entry_points_enforce_policy(self):
        unit = (FILESYSTEM / "root_init/etc/systemd/system/re3d-security.service").read_text()
        self.assertIn("Before=network-pre.target", unit)
        installer = (ASSETS / "install_auth.sh").read_text()
        self.assertIn("Requires=re3d-security.service", installer)
        self.assertIn("re3d-auth-revoke.path", installer)
        revoke = (FILESYSTEM / "root_init/etc/systemd/system/re3d-auth-revoke.service").read_text()
        self.assertIn("ExecStart=/bin/systemctl restart nginx.service", revoke)
        for name in ("re3d-session", "start_klipperscreen", "start_chromium_browser"):
            text = (FILESYSTEM / "opt/custompios/scripts" / name).read_text()
            self.assertIn("/etc/re3d-security/lockdown", text)
        session = (FILESYSTEM / "opt/custompios/scripts/re3d-session").read_text()
        self.assertLess(session.index('if [ -f "$LOCKDOWN" ]'), session.index('if [ "$MODE" = "klipperscreen" ]'))
        self.assertIn("exec cage -d --", session)
        update = (ROOT / "tools/re3d-os-update.sh").read_text(encoding="utf-8")
        self.assertIn("security_policy.py kiosk.html kiosk-client.js", update)
        self.assertIn("-name '*.path'", update)


if __name__ == "__main__":
    unittest.main()
