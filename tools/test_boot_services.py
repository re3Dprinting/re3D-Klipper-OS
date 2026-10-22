import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


SOURCE = (Path(__file__).resolve().parents[1] / 'src' / 'modules' / 'fullpageos'
          / 'filesystem' / 'home' / 'pi' / 'printer_data' / 'config' / 'src')


class BootServicesTests(unittest.TestCase):
    def test_printer_configuration_restarts_backends_without_interrupting_display(self):
        module = ast.parse((SOURCE / 'reload.py').read_text(encoding='utf-8'))
        reboot_services = next(node for node in module.body
                               if isinstance(node, ast.FunctionDef)
                               and node.name == 'reboot_services')
        os = Mock()
        namespace = {'os': os}
        exec(compile(ast.Module(body=[reboot_services], type_ignores=[]),
                     'reload.py', 'exec'), namespace)

        namespace['reboot_services']()

        self.assertEqual(os.system.call_args_list, [
            unittest.mock.call('service moonraker restart'),
            unittest.mock.call('service klipper restart'),
        ])


if __name__ == '__main__':
    unittest.main()
