import ast
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import urlencode

import jinja2


FILESYSTEM = Path(__file__).resolve().parents[1] / 'src/modules/fullpageos/filesystem'
SOURCE = FILESYSTEM / 'home/pi/printer_data/config/src'
CGI = FILESYSTEM / 'opt/mconfig/www/cgi-bin'
FLAG = 'slice_microswiss_hotends_enabled'


class HotendSelectionTests(unittest.TestCase):
    def setup_generator(self):
        module = ast.parse((SOURCE / 'setup_printer.py').read_text())
        setup = next(node for node in module.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == 'setup_fff_printer')
        installed = []
        namespace = {
            'FFF_PATH': SOURCE / 'fff', 'OUTPUT_PATH': Path('build'),
            'common_setup_printer': lambda *args: installed.clear(),
            'validate_and_return_config_param':
                lambda field, config, valid_selections, default:
                    config.get(field) if config.get(field) in valid_selections else default,
            'add_template_file': lambda source, target, *args: installed.append((source, target)),
            'is_valid_path': lambda path: path.exists(),
        }
        exec(compile(ast.Module(body=[setup], type_ignores=[]), 'setup_printer.py', 'exec'), namespace)
        return namespace['setup_fff_printer'], installed

    def test_generated_limits_and_default_off(self):
        setup, installed = self.setup_generator()
        for platform, limits in [('regular', (587, 587, 582)), ('xlt', (585, 740, 886)), ('terabot', None)]:
            for enabled in ['true', 'false', None, 'invalid']:
                for mesh in ['true', 'false']:
                    with self.subTest(platform=platform, enabled=enabled, mesh=mesh):
                        setup(
                            {FLAG: enabled, 'mesh_compensation_enabled': mesh}, 'archimajor', platform
                        )
                        overrides = [(source, target) for source, target in installed
                                     if 'slice_microswiss' in source.name]
                        self.assertEqual(bool(overrides), enabled == 'true' and limits is not None)
                        if not overrides:
                            continue
                        source, target = overrides[0]
                        self.assertGreater(target.name, 'mesh_' + platform + '.cfg')
                        config = configparser.ConfigParser()
                        config.read([SOURCE / 'fff/platform_specific' / ('archimajor_' + platform + '.cfg'), source])
                        self.assertEqual(tuple(config.getint('stepper_' + axis, 'position_max')
                                               for axis in ['x', 'y', 'z']), limits)
                        self.assertEqual(config.getint('stepper_y', 'position_endstop'), limits[1])
                        for axis in ['x', 'y', 'y1']:
                            self.assertEqual(config.getint('stepper_' + axis, 'rotation_distance'), 54)
                        self.assertEqual(config.getfloat('tmc5160 extruder', 'run_current'), 1.2)

    def test_filament_option_changes_only_both_diameters(self):
        setup, installed = self.setup_generator()
        extruders = SOURCE / 'fff/config/fff_extruders.cfg'
        baseline = configparser.ConfigParser(interpolation=None)
        baseline.read(extruders)
        for platform in ['regular', 'xlt', 'terabot']:
            for hotends in ['true', 'false']:
                for mesh in ['true', 'false']:
                    options = {FLAG: hotends, 'mesh_compensation_enabled': mesh}
                    setup(options, 'archimajor', platform)
                    default_files = list(installed)
                    for enabled in ['true', 'false', None, 'invalid']:
                        with self.subTest(platform=platform, hotends=hotends, mesh=mesh, enabled=enabled):
                            setup({**options, 'filament_175_enabled': enabled}, 'archimajor', platform)
                            overrides = [(source, target) for source, target in installed
                                         if 'filament_175' in source.name]
                            self.assertEqual(bool(overrides), enabled == 'true')
                            self.assertEqual([pair for pair in installed if pair not in overrides],
                                             default_files)
                            config = configparser.ConfigParser(interpolation=None)
                            config.read([extruders] + [source for source, _ in overrides])
                            expected = {section: dict(baseline[section]) for section in baseline.sections()}
                            for section in ['extruder', 'extruder1']:
                                expected[section]['filament_diameter'] = '1.75' if enabled == 'true' else '2.85'
                            self.assertEqual({section: dict(config[section]) for section in config.sections()},
                                             expected)
                            if overrides:
                                source, target = overrides[0]
                                self.assertGreater(target.name, extruders.name)
                                override = configparser.ConfigParser()
                                override.read(source)
                                self.assertEqual({section: dict(override[section])
                                                  for section in override.sections()},
                                                 {section: {'filament_diameter': '1.75'}
                                                  for section in ['extruder', 'extruder1']})

    def test_template_default(self):
        config = configparser.ConfigParser()
        config.read(SOURCE / 'common/master.cfg')
        self.assertFalse(config.getboolean('fff', FLAG))
        self.assertFalse(config.getboolean('fff', 'filament_175_enabled'))

    def test_mesh_moves_follow_configured_limit(self):
        environment = jinja2.Environment(variable_start_string='{', variable_end_string='}')
        for platform, limits in [('regular', [610, 587]), ('xlt', [760, 740])]:
            source = (SOURCE / 'fff/platform_specific' / ('mesh_' + platform + '.cfg')).read_text()
            moves = [line.strip().split('#')[0] for line in source.splitlines()
                     if 'Y{printer.configfile.settings.stepper_y.position_max' in line]
            self.assertEqual(len(moves), 4)
            for maximum in limits:
                rendered = environment.from_string('\n'.join(moves)).render(
                    printer={'configfile': {'settings': {'stepper_y': {'position_max': maximum}}}}
                )
                positions = [float(token[1:]) for line in rendered.splitlines()
                             for token in line.split() if token.startswith('Y')]
                self.assertEqual(positions, [maximum, maximum, maximum - 4, maximum])

    def test_cgi_round_trip_and_legacy_defaults(self):
        git_bash = Path('C:/Program Files/Git/bin/bash.exe')
        bash = str(git_bash) if git_bash.exists() else shutil.which('bash')
        if not bash:
            self.skipTest('Bash is required for CGI integration tests')
        core = (SOURCE / 'system_config/shell_commands/set_machine_type').read_text(encoding='utf-8')
        setter = (CGI / 'set_machine_type.sh').read_text(encoding='utf-8')
        getter = (CGI / 'get_machine_options.sh').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / '.master.cfg'

            def run(script, body=''):
                script = script.replace('/home/pi/printer_data/config/.master.cfg', config_path.as_posix())
                result = subprocess.run(
                    [bash, '-c', script], input=body, text=True, capture_output=True,
                    env={**os.environ, 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(body))},
                    check=True,
                )
                return result.stdout.split('\n\n', 1)[-1].strip()

            self.assertFalse(json.loads(run(getter))['slice_microswiss_hotends'])
            self.assertFalse(json.loads(run(getter))['filament_175'])
            for machine in ['Gigabot 4', 'Gigabot 4 XLT', 'Terabot 4',
                            'GigabotX 2', 'GigabotX XLT 2', 'TerabotX 2']:
                for enabled in ['true', 'false', None, 'invalid']:
                    fields = {'type': machine}
                    if enabled is not None:
                        fields['slice_microswiss_hotends'] = enabled
                        fields['filament_175'] = enabled
                    script = 'test_set_machine_type() {\n' + core + '\n}\n' + setter.replace(
                        '/usr/local/bin/set_machine_type', 'test_set_machine_type'
                    )
                    run(script, urlencode(fields))
                    expected = enabled == 'true' and machine in ['Gigabot 4', 'Gigabot 4 XLT', 'Terabot 4']
                    options = json.loads(run(getter))
                    self.assertEqual(options['slice_microswiss_hotends'], expected)
                    self.assertEqual(options['filament_175'], expected)
                    config = configparser.ConfigParser()
                    config.read(config_path)
                    self.assertEqual(config.has_option('fff', 'filament_175_enabled'),
                                     machine in ['Gigabot 4', 'Gigabot 4 XLT', 'Terabot 4'])
            config_path.write_text('[fff]\nplatform_type=regular\n')
            self.assertFalse(json.loads(run(getter))['slice_microswiss_hotends'])
            self.assertFalse(json.loads(run(getter))['filament_175'])


if __name__ == '__main__':
    unittest.main()