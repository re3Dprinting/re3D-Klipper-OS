import ast
import contextlib
import io
import logging
import os
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zlib

import patch_noncritical_mcu as patcher


class LegacyMCUTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.environ.get('KLIPPER_TEST_ROOT')
        if not source:
            raise unittest.SkipTest('Set KLIPPER_TEST_ROOT to Klipper 3ef760c18')
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name) / 'klipper'
        shutil.copytree(Path(source) / 'klippy', cls.root / 'klippy',
                        ignore=shutil.ignore_patterns('__pycache__'))
        for backup in cls.root.rglob('*' + patcher.BACKUP_SUFFIX):
            shutil.copyfile(backup, str(backup)[:-len(patcher.BACKUP_SUFFIX)])
            backup.unlink()
        cls.original = {
            path.relative_to(cls.root).as_posix(): path.read_bytes()
            for path in cls.root.rglob('*.py')
        }
        if patcher.is_refactored_mcu(str(cls.root)):
            raise AssertionError('Expected legacy MCU fixture')
        with contextlib.redirect_stdout(io.StringIO()):
            if not patcher.do_patch(str(cls.root)):
                raise AssertionError('Full patch failed on target revision')
        cls.mcu_tree = ast.parse((cls.root / 'klippy/mcu.py').read_text())

    def setUp(self):
        self.ffi = Mock()
        self.library = Mock()
        self.ffi.gc.side_effect = lambda pointer, free: pointer
        self.serial_module = Mock()
        self.serial_module.error = RuntimeError
        self.namespace = {
            'error': RuntimeError, 'logging': logging, 'os': os,
            'serialhdl': self.serial_module,
            'chelper': types.SimpleNamespace(
                get_ffi=lambda: (self.ffi, self.library)),
            'zlib': zlib, 'msgproto': types.SimpleNamespace(
                enumeration_error=ValueError),
        }
        mcu_class = next(node for node in self.mcu_tree.body
                         if isinstance(node, ast.ClassDef) and node.name == 'MCU')
        exec(compile(ast.Module(body=[mcu_class], type_ignores=[]),
                     'patched_mcu.py', 'exec'), self.namespace)
        self.reactor = Mock(NOW=0., NEVER=1.e30)
        self.reactor.monotonic.return_value = 10.
        self.printer = Mock()
        self.printer.get_reactor.return_value = self.reactor
        self.printer.get_start_args.return_value = {}
        self.config = Mock()
        self.config.error = ValueError
        self.config.get_printer.return_value = self.printer
        self.config.get_name.return_value = 'mcu sensor'
        self.config.getboolean.return_value = True
        self.config.get.side_effect = lambda name, default=None: (
            '/dev/serial/by-id/sensor' if name == 'serial' else default)
        self.config.getint.side_effect = lambda name, default, **kw: default
        self.config.getfloat.side_effect = lambda name, default, **kw: default
        self.config.getchoice.return_value = 'command'
        self.clock = Mock()
        self.mcu = self.namespace['MCU'](self.config, self.clock)

    def test_constructor_and_serial_owner(self):
        self.assertTrue(self.mcu.is_non_critical)
        self.assertFalse(self.mcu.non_critical_disconnected)
        self.assertIs(self.serial_module.SerialReader.call_args.kwargs['mcu'],
                      self.mcu)
        self.assertEqual(self.mcu.get_non_critical_reconnect_event_name(),
                         'noncritical_mcu_sensor:reconnected')

    def test_primary_mcu_rejected(self):
        self.config.get_name.return_value = 'mcu'
        with self.assertRaisesRegex(ValueError, 'Primary MCU'):
            self.namespace['MCU'](self.config, self.clock)

    def test_can_and_pipe_rejected(self):
        for settings in ({'canbus_uuid': '123'},
                         {'serial': '/tmp/klipper_host_mcu'}):
            with self.subTest(settings=settings):
                self.config.get.side_effect = (
                    lambda name, default=None: settings.get(name, default))
                with self.assertRaisesRegex(ValueError, 'UART/USB'):
                    self.namespace['MCU'](self.config, self.clock)

    def test_critical_mcu_unchanged(self):
        self.config.getboolean.return_value = False
        self.config.get_name.return_value = 'mcu'
        mcu = self.namespace['MCU'](self.config, self.clock)
        self.assertFalse(mcu.non_critical_disconnected)
        self.assertIsNone(mcu._non_critical_recon_timer)
        mcu._steppersync = Mock()
        self.clock.calibrate_clock.return_value = (0., 1000000.)
        self.clock.is_active.return_value = False
        mcu.check_active(1., 2.)
        self.printer.invoke_shutdown.assert_called_once()

    def test_timeout_disconnects_only_optional_mcu(self):
        self.mcu._steppersync = Mock()
        self.clock.calibrate_clock.return_value = (0., 1000000.)
        self.clock.is_active.return_value = False
        self.mcu.check_active(1., 2.)
        self.assertTrue(self.mcu.non_critical_disconnected)
        self.assertIsNone(self.mcu._steppersync)
        self.printer.invoke_shutdown.assert_not_called()
        self.clock.disconnect.assert_called_once()
        self.mcu._serial.disconnect.assert_called_once()
        self.mcu.handle_non_critical_disconnect()
        self.printer.send_event.assert_called_once_with(
            'noncritical_mcu_sensor:disconnected')

    def test_shutdown_callback_uses_reactor(self):
        self.mcu._handle_shutdown({})
        self.mcu._serial.disconnect.assert_not_called()
        callback = self.reactor.register_async_callback.call_args.args[0]
        callback(0.)
        self.assertTrue(self.mcu.non_critical_disconnected)
        self.printer.invoke_async_shutdown.assert_not_called()

    def test_spontaneous_restart_uses_reactor(self):
        self.mcu._handle_starting({})
        self.reactor.register_async_callback.assert_called_once()
        self.printer.invoke_async_shutdown.assert_not_called()

    def test_missing_device_retries(self):
        self.mcu.non_critical_disconnected = True
        self.mcu._mcu_identify = Mock()
        with patch.object(os.path, 'exists', return_value=False):
            self.assertEqual(self.mcu._non_critical_recon_event(10.), 12.)
        self.mcu._mcu_identify.assert_not_called()

    def test_reconnect_success(self):
        self.mcu.non_critical_disconnected = True
        calls = Mock()
        self.mcu._mcu_identify = calls.identify
        self.mcu._connect = calls.connect
        self.mcu._ready = calls.ready
        with patch.object(os.path, 'exists', return_value=True):
            result = self.mcu._non_critical_recon_event(10.)
        self.assertEqual(result, self.reactor.NEVER)
        self.assertFalse(self.mcu.non_critical_disconnected)
        self.assertEqual([call[0] for call in calls.mock_calls],
                         ['identify', 'connect', 'ready'])
        self.printer.send_event.assert_called_once_with(
            'noncritical_mcu_sensor:reconnected')

    def test_reconnect_failure_cleans_up_and_retries(self):
        for stage in ('_mcu_identify', '_connect'):
            with self.subTest(stage=stage):
                self.mcu.non_critical_disconnected = True
                self.mcu._mcu_identify = Mock()
                self.mcu._connect = Mock()
                getattr(self.mcu, stage).side_effect = RuntimeError('offline')
                with patch.object(os.path, 'exists', return_value=True), \
                     self.assertLogs(level='ERROR'):
                    result = self.mcu._non_critical_recon_event(10.)
                self.assertEqual(result, 12.)
                self.assertTrue(self.mcu.non_critical_disconnected)
                self.assertFalse(self.mcu._non_critical_reconnecting)
                self.assertIsNone(self.mcu._steppersync)
        self.assertEqual(self.clock.disconnect.call_count, 2)
        self.assertEqual(self.mcu._serial.disconnect.call_count, 2)
        self.printer.send_event.assert_not_called()

    def test_global_shutdown_cancels_reconnection(self):
        self.mcu.non_critical_disconnected = True
        self.mcu._shutdown()
        self.assertEqual(self.mcu._non_critical_recon_event(10.),
                         self.reactor.NEVER)
        self.reactor.update_timer.assert_called_with(
            self.mcu._non_critical_recon_timer, self.reactor.NEVER)

    def test_shutdown_during_reconnection(self):
        self.mcu.non_critical_disconnected = True
        self.mcu._mcu_identify = Mock(side_effect=self.mcu._shutdown)
        self.mcu._connect = Mock()
        with patch.object(os.path, 'exists', return_value=True):
            self.assertEqual(self.mcu._non_critical_recon_event(10.),
                             self.reactor.NEVER)
        self.mcu._connect.assert_not_called()
        self.mcu._serial.disconnect.assert_called_once()
        self.printer.send_event.assert_not_called()

    def test_reconnect_cannot_restart_printer(self):
        self.mcu._non_critical_reconnecting = True
        with self.assertRaisesRegex(RuntimeError, 'CRC mismatch'):
            self.mcu._check_restart('CRC mismatch')
        self.printer.request_exit.assert_not_called()

    def test_config_rebuild_is_stable(self):
        resolver = self.printer.lookup_object.return_value.get_pin_resolver()
        resolver.update_command.side_effect = lambda command: command
        self.mcu._config_cmds = ['base_config']
        self.mcu._oid_count = 1
        self.mcu._reserved_move_slots = 2

        def build_config():
            self.mcu.add_config_cmd('sensor oid=%d' % self.mcu.create_oid())
            self.mcu.add_config_cmd('init_sensor', is_init=True)
            self.mcu.request_move_queue_slot()

        self.mcu.register_config_callback(build_config)
        snapshots = []
        for attempt in range(3):
            self.mcu._send_config(None)
            snapshots.append((self.mcu._oid_count,
                              self.mcu._reserved_move_slots,
                              list(self.mcu._config_cmds),
                              list(self.mcu._init_cmds)))
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        self.assertEqual(snapshots[0][:2], (2, 3))

    def test_full_patch_compiles_and_is_idempotent(self):
        before = {}
        for path in self.root.rglob('*.py'):
            source = path.read_bytes()
            compile(source, str(path), 'exec')
            before[path] = source
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(patcher.do_patch(str(self.root)))
        for path, source in before.items():
            self.assertEqual(path.read_bytes(), source, str(path))

    def test_original_connection_methods_across_reconnects(self):
        self.namespace['CommandWrapper'] = Mock()
        query_wrapper = self.namespace['CommandQueryWrapper'] = Mock()
        query_wrapper.return_value.send.side_effect = [
            {'is_config': configured, 'is_shutdown': False,
             'move_count': 500, 'crc': 0}
            for configured in (False, True, False, True, False, True)
        ]
        constants = {'CLOCK_FREQ': 1000000., 'STATS_SUMSQ_BASE': 256.}
        parser = self.mcu._serial.get_msgparser.return_value
        parser.get_constants.return_value = constants
        parser.get_constant_float.side_effect = constants.__getitem__
        parser.get_constant.side_effect = lambda name, default=None: default
        parser.get_version_info.return_value = ('test-version', 'test-build')
        parser.get_messages.return_value = []
        resolver = self.printer.lookup_object.return_value.get_pin_resolver()
        resolver.update_command.side_effect = lambda command: command
        self.clock.get_clock.side_effect = lambda time: time * 1000000.
        self.mcu._config_cmds = ['sensor_config']
        self.mcu._mcu_identify()
        self.mcu._connect()
        self.mcu._ready()
        initial_commands = list(self.mcu._config_cmds)
        for attempt in range(2):
            self.mcu.handle_non_critical_disconnect()
            with patch.object(os.path, 'exists', return_value=True):
                self.assertEqual(self.mcu._non_critical_recon_event(10.),
                                 self.reactor.NEVER)
            self.assertFalse(self.mcu.non_critical_disconnected)
            self.assertEqual(self.mcu._config_cmds, initial_commands)
        self.assertEqual(self.mcu._serial.connect_uart.call_count, 3)
        self.assertEqual(self.library.steppersync_alloc.call_count, 3)
        self.printer.invoke_shutdown.assert_not_called()
        self.printer.request_exit.assert_not_called()

    def test_revert_restores_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'klipper'
            shutil.copytree(self.root, root)
            with contextlib.redirect_stdout(io.StringIO()):
                patcher.do_revert(str(root))
            for name, content in self.original.items():
                self.assertEqual((root / name).read_bytes(), content, name)

    def test_unknown_legacy_layout_fails_without_modifying_mcu(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'klippy').mkdir()
            target = root / 'klippy/mcu.py'
            source = self.original['klippy/mcu.py'].replace(
                b'    def check_active(', b'    def different_check_active(')
            source = source.replace(b'        self._is_timeout = True',
                                    b'        self._is_timeout = bool(1)')
            target.write_bytes(source)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(patcher.patch_mcu_legacy(str(root)))
            self.assertEqual(target.read_bytes(), source)


if __name__ == '__main__':
    unittest.main()