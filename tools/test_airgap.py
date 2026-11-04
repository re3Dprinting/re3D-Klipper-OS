import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM = ROOT / "src/modules/fullpageos/filesystem"
ASSETS = FILESYSTEM / "opt/mconfig"
spec = importlib.util.spec_from_file_location("airgap", ASSETS / "airgap.py")
airgap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(airgap)
require_pin = airgap.require_pin


class AirgapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, value in {
            "ENABLED": self.root / "etc/enabled",
            "APPLIED": self.root / "run/enabled",
            "RADIOS": self.root / "etc/radios.json",
            "RULES": self.root / "udev/99-re3d-airgap.rules",
        }.items():
            patcher = patch.object(airgap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sync = patch.object(airgap, "sync_directory").start()
        self.pin_check = patch.object(airgap, "require_pin").start()
        self.addCleanup(patch.stopall)

    def test_saving_is_persistent_but_does_not_apply_until_reboot(self):
        with patch.object(airgap, "radio_states", return_value={"phy0": 0}), \
                patch.object(airgap, "run") as run:
            airgap.save(True)
            self.assertTrue(airgap.ENABLED.exists())
            self.assertFalse(airgap.APPLIED.exists())
            self.assertEqual(json.loads(airgap.RADIOS.read_text()), {"phy0": 0})
            run.assert_not_called()
            airgap.APPLIED.parent.mkdir()
            airgap.APPLIED.touch()
            airgap.save(False)
            self.assertFalse(airgap.ENABLED.exists())
            self.assertTrue(airgap.APPLIED.exists())
            self.assertTrue(airgap.RADIOS.exists())
            self.sync.assert_called()

    def test_invalid_enable_values_never_change_state(self):
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value):
                self.assertRaises(ValueError, airgap.save, value)
                self.assertFalse(airgap.ENABLED.exists())

    def test_cancelling_pending_enable_does_not_restore_stale_radio_preferences(self):
        with patch.object(airgap, "radio_states", return_value={"phy0": 0}):
            airgap.save(True)
        airgap.save(False)
        self.assertFalse(airgap.RADIOS.exists())
        with patch.object(airgap, "set_radio") as radio:
            airgap.apply()
            radio.assert_not_called()

    def test_inactive_boot_leaves_networking_untouched(self):
        with patch.object(airgap, "run") as run:
            airgap.apply()
            run.assert_not_called()
        self.assertFalse(airgap.RULES.exists())

    def test_boot_rejects_missing_pin_before_marking_isolation_applied(self):
        airgap.ENABLED.parent.mkdir()
        airgap.ENABLED.touch()
        self.pin_check.side_effect = ValueError("Missing touchscreen PIN")
        with patch.object(airgap, "radio_states", return_value={}), \
                patch.object(airgap, "run", return_value=Mock(stdout='{"nftables": []}')) as run:
            self.assertRaisesRegex(ValueError, "PIN", airgap.apply)
            self.assertEqual(run.call_args_list[1].args, ("nft", "-f", "-"))
        self.assertFalse(airgap.APPLIED.exists())

    def test_boot_reads_the_existing_shared_pin_credential(self):
        state = self.root / "password.json"
        credential = {"salt": "00" * 32, "hash": "11" * 64}
        with patch.object(sys, "path", [str(ASSETS), *sys.path]), \
                patch.object(airgap, "AUTH_STATE", state):
            self.assertRaisesRegex(ValueError, "PIN", require_pin)
            state.write_text(json.dumps({**credential, "mode": "basic", "pin": None}))
            self.assertRaisesRegex(ValueError, "PIN", require_pin)
            state.write_text(json.dumps({**credential, "mode": "basic", "pin": credential}))
            require_pin()

    def test_boot_blocks_ipv4_ipv6_and_forwarding_without_established_bypass(self):
        rules = airgap.firewall_rules()
        self.assertIn("table inet re3d_airgap", rules)
        self.assertEqual(rules.count("policy drop;"), 3)
        self.assertIn('iifname "lo" accept;', rules)
        self.assertIn('oifname "lo" accept;', rules)
        self.assertNotIn("established", rules)
        self.assertNotIn("re3d_lockdown", rules)
        self.assertTrue(airgap.firewall_rules(True).startswith("delete table inet re3d_airgap;"))

    def test_active_boot_applies_firewall_first_and_preserves_input_exceptions(self):
        airgap.ENABLED.parent.mkdir()
        airgap.ENABLED.touch()
        with patch.object(airgap, "radio_states", return_value={}), \
                patch.object(airgap, "block_radios") as radios, \
                patch.object(airgap, "disable_links") as links, \
                patch.object(airgap, "run", return_value=Mock(stdout='{"nftables": []}')) as run:
            airgap.apply()
            self.assertEqual(run.call_args_list[0], call("nft", "-j", "list", "tables"))
            self.assertEqual(run.call_args_list[1].args, ("nft", "-f", "-"))
            self.assertIn("systemd-networkd.socket", run.call_args_list[2].args)
            self.assertIn("bluetooth.service", run.call_args_list[2].args)
            self.assertIn("getty@.service", run.call_args_list[2].args)
            radios.assert_called_once()
            links.assert_called_once()
        rules = airgap.RULES.read_text()
        self.assertIn('ENV{LIBINPUT_IGNORE_DEVICE}="1"', rules)
        self.assertIn('ENV{ID_INPUT_TOUCHSCREEN}!="1"', rules)
        self.assertIn('KERNEL!="lo"', rules)
        self.assertNotIn("usb-storage", rules)
        self.assertNotIn("ttyACM", rules)
        self.assertTrue(airgap.APPLIED.exists())

    def test_policy_failure_does_not_publish_success_marker(self):
        airgap.ENABLED.parent.mkdir()
        airgap.ENABLED.touch()
        with patch.object(airgap, "radio_states", return_value={}), \
                patch.object(airgap, "run", side_effect=subprocess.CalledProcessError(1, "nft")):
            self.assertRaises(subprocess.CalledProcessError, airgap.apply)
        self.assertFalse(airgap.APPLIED.exists())

    def test_loopback_is_never_disabled(self):
        with patch.object(airgap, "run") as run:
            airgap.disable_link("lo")
            run.assert_not_called()
        for name in ("", "../eth0", "-x"):
            self.assertRaises(ValueError, airgap.disable_link, name)

    def test_usb_wifi_ethernet_and_virtual_interfaces_are_all_disabled(self):
        for name in ("eth0", "wlan0", "usb0", "enx1234", "br0", "tun0"):
            with self.subTest(name=name), patch.object(Path, "read_text", return_value="0x1003"), \
                    patch.object(airgap, "run") as run:
                airgap.disable_link(name)
                run.assert_called_once_with("ip", "link", "set", "dev", name, "down")

    def test_touch_and_nonkeyboard_input_are_preserved(self):
        self.assertTrue(airgap.keyboard_device({"ID_INPUT_KEYBOARD": "1"}))
        for device in ({}, {"ID_INPUT_MOUSE": "1"}, {"ID_INPUT_TOUCHSCREEN": "1"},
                       {"ID_INPUT_KEYBOARD": "1", "ID_INPUT_TOUCHSCREEN": "1"}):
            self.assertFalse(airgap.keyboard_device(device))

    def test_rfkill_packet_blocks_all_types_and_restores_saved_states(self):
        device = self.root / "rfkill"
        device.touch()
        with device.open("wb") as stream:
            with patch.object(Path, "exists", return_value=True), \
                    patch.object(Path, "open", return_value=stream):
                airgap.block_radios()
        self.assertEqual(device.read_bytes(), struct.pack("IBBBB", 0, 0, 3, 1, 0))
        airgap.RADIOS.parent.mkdir()
        airgap.RADIOS.write_text('{"phy0": 1}')
        sys_radio = self.root / "rfkill7"
        sys_radio.mkdir()
        (sys_radio / "name").write_text("phy0")
        with patch.object(airgap, "set_radio") as radio, patch.object(Path, "glob", return_value=[sys_radio]):
            airgap.restore_radios()
            self.assertEqual(radio.call_args_list, [call(blocked=0), call(index=7, operation=2, blocked=1)])
        self.assertFalse(airgap.RADIOS.exists())

    def test_root_helper_rejects_arbitrary_fields_and_malformed_requests(self):
        for body in (b'{"enabled":true,"command":"anything"}\n', b'{"enabled":"true"}\n',
                     b'[]\n', b'not json\n', b'{"enabled":true}', b'x' * 1025 + b'\n'):
            handler = object.__new__(airgap.Handler)
            handler.request = Mock()
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            handler.handle()
            self.assertEqual(json.loads(handler.wfile.getvalue())["status"], 400)
            self.assertFalse(airgap.ENABLED.exists())

    def test_root_helper_io_failure_is_explicit(self):
        handler = object.__new__(airgap.Handler)
        handler.request = Mock()
        handler.rfile = io.BytesIO(b'{"enabled":true}\n')
        handler.wfile = io.BytesIO()
        with patch.object(airgap, "save", side_effect=OSError("disk full")), self.assertLogs(level="ERROR"):
            handler.handle()
        result = json.loads(handler.wfile.getvalue())
        self.assertEqual(result["status"], 500)
        self.assertNotIn("ok", result)

    def test_keyboard_grab_is_exclusive_and_new_devices_are_picked_up(self):
        keyboard, touch = self.root / "event0", self.root / "event1"
        keyboard.touch()
        touch.touch()
        ioctl = Mock()
        def properties(*args, **kwargs):
            return Mock(stdout="ID_INPUT_KEYBOARD=1\n" if args[-1] == str(keyboard)
                        else "ID_INPUT_TOUCHSCREEN=1\n")
        with patch.dict(sys.modules, {"fcntl": types.SimpleNamespace(ioctl=ioctl)}), \
                patch.object(os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 0), create=True), \
                patch.object(Path, "glob", return_value=[keyboard, touch]), \
                patch.object(airgap, "run", side_effect=properties):
            keyboards = airgap.Keyboards()
            try:
                keyboards.scan()
                self.assertEqual(set(keyboards.grabbed), {keyboard})
                ioctl.assert_called_once_with(keyboards.grabbed[keyboard], 0x40044590, 1)
                keyboards.scan()
                self.assertEqual(ioctl.call_count, 1)
                with patch.object(Path, "glob", return_value=[touch]):
                    keyboards.scan()
                self.assertFalse(keyboards.grabbed)
            finally:
                keyboards.close()

    def test_installer_gates_network_and_display_startup_without_build_changes(self):
        units = FILESYSTEM / "root_init/etc/systemd/system"
        policy = (units / "re3d-airgap.service").read_text()
        helper = (units / "re3d-airgap-helper.service").read_text()
        installer = (ASSETS / "install_auth.sh").read_text()
        self.assertIn("DefaultDependencies=no", policy)
        self.assertIn("Before=network-pre.target", policy)
        self.assertIn("After=local-fs.target nftables.service systemd-udevd.service", policy)
        self.assertIn("Requires=re3d-airgap.service", helper)
        self.assertIn("Type=notify", helper)
        self.assertIn("Requires=re3d-airgap-helper.service", installer)
        self.assertIn("ConditionPathExists=!/etc/re3d-airgap/enabled", installer)
        self.assertIn("ConditionPathExists=!/run/re3d-airgap/enabled", installer)
        self.assertIn("NetworkManager systemd-networkd", installer)
        self.assertIn("systemctl enable re3d-airgap.service re3d-airgap-helper.service", installer)


if __name__ == "__main__":
    unittest.main()
