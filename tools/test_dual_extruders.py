import ast
import configparser
import copy
from pathlib import Path
import shlex
import unittest

import jinja2


CONFIG_ROOT = (
    Path(__file__).resolve().parents[1]
    / 'src/modules/fullpageos/filesystem/home/pi/printer_data/config/src'
)


class DualExtruderTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read(CONFIG_ROOT / 'fgf/custom/fgf_dual_extruders.cfg')
        self.environment = jinja2.Environment(
            block_start_string='{%', block_end_string='%}',
            variable_start_string='{', variable_end_string='}',
            undefined=jinja2.StrictUndefined,
        )
        self.state = {
            key.removeprefix('variable_'): ast.literal_eval(value)
            for key, value in self.config['gcode_macro SYNC_EXTRUDERS'].items()
            if key.startswith('variable_')
        }
        self.printer = {
            'gcode_macro SYNC_EXTRUDERS': self.state,
            'configfile': {'settings': {
                'extruder': {'rotation_distance': 3.846},
                'extruder4': {'rotation_distance': 5.0},
                'extruder5': {}, 'extruder6': {},
            }},
            'extruder': {'pressure_advance': 0.12, 'smooth_time': 0.04},
            'extruder4': {
                'pressure_advance': 0.05, 'smooth_time': 0.06,
                'can_extrude': True,
            },
            'extruder5': {'can_extrude': True},
            'extruder6': {'can_extrude': True},
        }
        self.distances = {'extruder': 3.846, 'extruder4': 5.0}
        self.queues = {'extruder': 'extruder', 'extruder4': 'extruder4'}
        self.active = 'extruder'
        self.commands = []

    def execute(self, name, **params):
        def fail(message):
            raise ValueError(message)

        template = self.environment.from_string(
            self.config['gcode_macro ' + name]['gcode']
        )
        rendered = template.render(
            printer=copy.deepcopy(self.printer), params=params,
            action_raise_error=fail,
        )
        for line in rendered.splitlines():
            tokens = shlex.split(line)
            if not tokens:
                continue
            command = tokens[0]
            arguments = dict(token.split('=', 1) for token in tokens[1:])
            self.commands.append(command)
            if 'gcode_macro ' + command in self.config:
                self.execute(command, **arguments)
            elif command == 'M400':
                pass
            elif command == 'SET_GCODE_VARIABLE':
                self.printer['gcode_macro ' + arguments['MACRO']][
                    arguments['VARIABLE']
                ] = ast.literal_eval(arguments['VALUE'])
            elif command == 'ACTIVATE_EXTRUDER':
                self.active = arguments['EXTRUDER']
            elif command == 'SYNC_EXTRUDER_MOTION':
                self.queues[arguments['EXTRUDER']] = arguments['MOTION_QUEUE']
            elif command == 'SET_EXTRUDER_ROTATION_DISTANCE':
                self.distances[arguments['EXTRUDER']] = float(arguments['DISTANCE'])
            elif command == 'SET_PRESSURE_ADVANCE':
                self.printer[arguments['EXTRUDER']].update(
                    pressure_advance=float(arguments['ADVANCE']),
                    smooth_time=float(arguments['SMOOTH_TIME']),
                )
            else:
                self.fail('Unexpected command: ' + command)

    def test_ratio_always_uses_first_motor(self):
        self.execute('T1')
        self.execute('SYNC_EXTRUDERS', RATIO='1.5')
        self.assertEqual(self.active, 'extruder')
        self.assertEqual(self.queues['extruder4'], 'extruder')
        self.assertAlmostEqual(
            self.distances['extruder'] / self.distances['extruder4'], 1.5
        )
        self.assertEqual(self.printer['extruder4']['pressure_advance'], 0.12)

    def test_repeated_and_live_ratios_do_not_compound(self):
        self.execute('SYNC_EXTRUDERS', RATIO='1.5')
        self.execute('SYNC_EXTRUDERS', RATIO='1.5')
        self.assertAlmostEqual(self.distances['extruder4'], 2.564)
        self.execute('SET_EXTRUDER_SYNC_RATIO', RATIO='0.5')
        self.assertAlmostEqual(self.distances['extruder4'], 7.692)

    def test_independent_selection_restores_follower(self):
        for tool, motor in [('T0', 'extruder'), ('T1', 'extruder4')]:
            with self.subTest(tool=tool):
                self.execute('SYNC_EXTRUDERS', RATIO='2')
                self.execute('SYNC_EXTRUDERS', RATIO='3')
                self.execute(tool)
                self.assertFalse(self.state['synced'])
                self.assertEqual(self.active, motor)
                self.assertEqual(self.queues['extruder4'], 'extruder4')
                self.assertEqual(self.distances['extruder4'], 5.0)
                self.assertEqual(self.printer['extruder4']['pressure_advance'], 0.05)
                self.assertEqual(self.printer['extruder4']['smooth_time'], 0.06)

    def test_stored_ratio_and_unsync_are_idempotent(self):
        self.execute('SET_EXTRUDER_SYNC_RATIO', RATIO='1.5')
        self.assertFalse(self.state['synced'])
        self.assertEqual(self.distances['extruder4'], 5.0)
        self.execute('SYNC_EXTRUDERS')
        self.assertAlmostEqual(self.distances['extruder4'], 2.564)
        self.execute('UNSYNC_EXTRUDERS')
        self.execute('UNSYNC_EXTRUDERS')
        self.assertEqual(self.distances['extruder4'], 5.0)
        self.execute('SYNC_EXTRUDERS')
        self.assertAlmostEqual(self.distances['extruder4'], 2.564)

    def test_invalid_ratios_do_not_execute_commands(self):
        for name in ['SYNC_EXTRUDERS', 'SET_EXTRUDER_SYNC_RATIO']:
            for ratio in ['0', '-1', 'bad', 'nan', 'inf', '-inf', '']:
                with self.subTest(name=name, ratio=ratio):
                    with self.assertRaises(ValueError):
                        self.execute(name, RATIO=ratio)
        with self.assertRaises(ValueError):
            self.execute('SET_EXTRUDER_SYNC_RATIO')
        with self.assertRaises(ValueError):
            self.execute('SYNC_EXTRUDERS', RATIO='1e-320')
        self.assertEqual(self.commands, [])

    def test_cold_follower_zones_block_sync(self):
        self.printer['configfile']['settings']['extruder7'] = {}
        self.printer['extruder7'] = {'can_extrude': True}
        for zone in ['extruder4', 'extruder5', 'extruder6', 'extruder7']:
            with self.subTest(zone=zone):
                self.printer[zone]['can_extrude'] = False
                with self.assertRaisesRegex(ValueError, zone):
                    self.execute('SYNC_EXTRUDERS')
                self.printer[zone]['can_extrude'] = True
        self.assertEqual(self.commands, [])

    def test_sync_drains_moves_before_switching(self):
        self.execute('SYNC_EXTRUDERS')
        self.assertEqual(self.commands[0], 'M400')
        self.commands.clear()
        self.execute('T1')
        self.assertEqual(self.commands[:2], ['UNSYNC_EXTRUDERS', 'M400'])
        self.assertEqual(self.commands[-1], 'ACTIVATE_EXTRUDER')

    def test_machine_selection_installs_macros_only_for_dual(self):
        module = ast.parse((CONFIG_ROOT / 'setup_printer.py').read_text())
        setup = next(node for node in module.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == 'setup_fgf_printer')
        installed = []
        namespace = {
            'FGF_PATH': CONFIG_ROOT / 'fgf', 'OUTPUT_PATH': Path('build'),
            'common_setup_printer': lambda *args: None,
            'validate_and_return_config_param':
                lambda field, config, valid_selections, default: config.get(field, default),
            'add_template_file': lambda source, *args: installed.append(source),
        }
        exec(compile(ast.Module(body=[setup], type_ignores=[]), 'setup_printer.py', 'exec'), namespace)
        dual_template = CONFIG_ROOT / 'fgf/custom/fgf_dual_extruders.cfg'
        for enabled in ['true', 'false']:
            installed.clear()
            namespace['setup_fgf_printer'](
                {'dual_extruder_enabled': enabled}, 'archimajor', 'regular'
            )
            self.assertEqual(dual_template in installed, enabled == 'true')


if __name__ == '__main__':
    unittest.main()