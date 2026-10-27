import ast
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILESYSTEM = ROOT / 'src/modules/fullpageos/filesystem'
SOURCE = FILESYSTEM / 'home/pi/printer_data/config/src'


class MainsailThemeTests(unittest.TestCase):
    def test_palette_matches_configurator(self):
        configurator = (FILESYSTEM / 'opt/mconfig/www/index.html').read_text(encoding='utf-8')
        theme = (SOURCE / 'common/mainsail.css').read_text(encoding='utf-8')
        for token in ['brand', 'bg', 'surface-0', 'surface-1', 'surface-2', 'surface-3',
                      'border', 'border-2', 'fg', 'muted', 'dim']:
            with self.subTest(token=token):
                expected = re.search(r'--' + token + r':\s*([^;]+);', configurator)[1]
                actual = re.search(r'--re3d-' + token + r':\s*([^;]+);', theme)[1]
                self.assertEqual(actual, expected)
        for deposition in ['fff', 'fgf']:
            defaults = json.loads((SOURCE / deposition / '.theme/default.json').read_text())
            self.assertEqual(defaults['uiSettings']['primary'], '#E7C547')

    def test_industrial_geometry_headers_and_label_casing(self):
        theme = (SOURCE / 'common/mainsail.css').read_text(encoding='utf-8')

        def declarations(selector):
            return re.search(re.escape(selector) + r'\s*\{([^}]+)\}', theme)[1]

        for token, expected in [('radius', 10), ('radius-sm', 8)]:
            radius = re.search(r'--re3d-' + token + r':\s*(\d+)px;', theme)
            self.assertEqual(int(radius[1]), expected)
        card = declarations(
            '.v-application.theme--dark .v-card:not(.error):not(.warning):not(.success):not(.info)')
        header = declarations(
            '.v-application.theme--dark .panel > .v-toolbar:not(.error):not(.warning):not(.success):not(.info)')
        self.assertIn('background-color: var(--re3d-surface-1)', card)
        self.assertIn('background-color: var(--re3d-surface-2)', header)
        for selector in ['.theme--dark.v-navigation-drawer .v-list-item__title',
                         '.theme--dark.v-btn']:
            self.assertIn('text-transform: uppercase;', declarations(selector))
        buttons = declarations('.theme--dark.v-btn:not(.v-btn--round):not(.v-btn--tile)')
        self.assertIn('border-radius: var(--re3d-radius-sm);', buttons)

    def test_sidebar_pattern_is_subtle_and_does_not_intercept_navigation(self):
        theme = (SOURCE / 'common/mainsail.css').read_text(encoding='utf-8')
        selector = '.theme--dark.v-navigation-drawer .v-navigation-drawer__image'
        pattern = re.search(re.escape(selector) + r'\s*\{([^}]+)\}', theme)[1]
        self.assertIn('display: block;', pattern)
        opacity = float(re.search(r'opacity:\s*([\d.]+);', pattern)[1])
        self.assertGreater(opacity, 0)
        self.assertLessEqual(opacity, .65)
        self.assertIn('pointer-events: none;', pattern)

    def test_generated_themes_include_shared_styles_and_machine_labels(self):
        module = ast.parse((SOURCE / 'setup_printer.py').read_text())
        functions = [node for node in module.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'add_platform_specific', 'add_board_pinmap',
                                       'add_board_specific', 'add_config', 'add_theme',
                                       'common_setup_printer'}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'build'
            theme = Path(directory) / '.theme'

            def copy_template(source, target, replace=False):
                if source.exists() and (replace or not target.exists()):
                    shutil.copyfile(source, target)

            namespace = {
                'shutil': shutil, 'os': __import__('os'),
                'OUTPUT_PATH': output, 'THEME_PATH': theme,
                'COMMON_PATH': SOURCE / 'common',
                'PINMAP_PATH': SOURCE / 'common/board_pinmap',
                'KLIPPER_CONFIG_PATH': Path(directory),
                'is_valid_path': lambda path: path.exists(),
                'add_template_file': copy_template,
            }
            exec(compile(ast.Module(body=functions, type_ignores=[]),
                         'setup_printer.py', 'exec'), namespace)
            for deposition, labels in [('fff', ['Left Heater', 'Right Heater']),
                                       ('fgf', ['Bottom Heater', 'Middle Heater', 'Top Heater'])]:
                for board in ['archimajor', 'azteeg']:
                    for platform in ['regular', 'xlt', 'terabot']:
                        with self.subTest(deposition=deposition, board=board, platform=platform):
                            namespace['common_setup_printer'](SOURCE / deposition, board, platform)
                            self.assertEqual((theme / 'mainsail.css').read_bytes(),
                                             (SOURCE / 'common/mainsail.css').read_bytes())
                            custom = (theme / 'custom.css').read_text()
                            imports = re.findall(r'@import url\("([^"]+)"\);', custom)
                            self.assertEqual(imports, ['update_manager.css', 'mainsail.css'])
                            for imported in imports:
                                self.assertTrue((theme / imported).is_file())
                            for label in labels:
                                self.assertIn('content: "' + label + '"', custom)
                            self.assertIn(' v1.4.3', custom)

    def test_updater_deploys_theme_generator_and_common_assets(self):
        update = (ROOT / 'tools/re3d-os-update.sh').read_text(encoding='utf-8')
        self.assertIn('config/src/setup_printer.py"', update)
        self.assertIn('install -m 0644 -o pi -g pi "${SRC_SETUP}" "${DST_SETUP}"', update)
        self.assertIn('"${SRC_COMMON}/"', update)
        self.assertIn('"${DST_COMMON}/"', update)


if __name__ == '__main__':
    unittest.main()
