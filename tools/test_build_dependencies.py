import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'src' / 'modules'
          / 'fullpageos' / 'start_chroot_script')


class MoonrakerPipTests(unittest.TestCase):
    def run_install(self, failures, fallback_status=0):
        git_bash = Path(r'C:\Program Files\Git\bin\bash.exe')
        bash = str(git_bash) if git_bash.exists() else shutil.which('bash')
        if not bash:
            self.skipTest('Bash is required for build dependency tests')
        source = SOURCE.read_text(encoding='utf-8')
        function = ('  _moonraker_pip_install() {\n'
                    + source.split('  _moonraker_pip_install() {\n', 1)[1]
                    .split('\n  }\n', 1)[0] + '\n  }\n')
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            python = home / 'moonraker-env' / 'bin' / 'python'
            python.parent.mkdir(parents=True)
            python.write_text('''#!/usr/bin/env bash
attempt=0
if [ -f "$HOME/attempt" ]; then
  read -r attempt < "$HOME/attempt"
fi
attempt=$((attempt + 1))
printf "%s\\n" "$attempt" > "$HOME/attempt"
printf "%s\\n" ATTEMPT "${PIP_CONFIG_FILE-unset}" "${PIP_EXTRA_INDEX_URL-unset}" "$@" >> "$HOME/calls"
if [ "${PIP_CONFIG_FILE-}" = /dev/null ]; then
  exit "$FALLBACK_STATUS"
fi
if [ "$attempt" -le "$FAILURES" ]; then
  exit 1
fi
''', encoding='utf-8')
            python.chmod(0o755)
            result = subprocess.run(
                [bash, '-c', 'set -e\nexport HOME="$PWD/home"\n'
                 'sleep() { :; }\n' + function
                 + '_moonraker_pip_install --no-cache-dir -r "requirements file.txt"\n'
                 'echo installed > "$HOME/requirements-hash"\n'],
                cwd=directory, text=True, capture_output=True,
                env={**os.environ, 'FAILURES': str(failures),
                     'FALLBACK_STATUS': str(fallback_status),
                     'PIP_CONFIG_FILE': 'original-pip.conf',
                     'PIP_EXTRA_INDEX_URL': 'https://www.piwheels.org/simple'},
            )
            calls = [call.splitlines() for call in
                     (home / 'calls').read_text().split('ATTEMPT\n')[1:]]
            return result, calls, (home / 'requirements-hash').exists()

    def test_primary_success_does_not_retry(self):
        result, calls, installed = self.run_install(failures=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(installed)
        self.assertEqual(calls, [
            ['original-pip.conf', 'https://www.piwheels.org/simple',
             '-m', 'pip', 'install', '--no-cache-dir', '-r', 'requirements file.txt'],
        ])

    def test_transient_failure_retries_without_dropping_arm_wheel_index(self):
        result, calls, installed = self.run_install(failures=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(installed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertIn('attempt 1 failed', result.stderr)

    def test_persistent_index_failure_falls_back_to_pypi_only(self):
        result, calls, installed = self.run_install(failures=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(installed)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1], [
            '/dev/null', 'unset', '-m', 'pip', 'install',
            '--index-url', 'https://pypi.org/simple',
            '--no-cache-dir', '-r', 'requirements file.txt',
        ])
        self.assertIn('without piwheels', result.stderr)

    def test_failed_fallback_stops_before_marking_requirements_installed(self):
        result, calls, installed = self.run_install(failures=2, fallback_status=7)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertFalse(installed)


if __name__ == '__main__':
    unittest.main()
