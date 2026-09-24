import configparser
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
import uuid


DATA = Path(__file__).resolve().parents[1] / 'calibration_data/input_shaper'
CONFIG = Path('/home/pi/printer_data/config/standalone.cfg')
BACKUPS = Path('/home/pi/printer_data/config/.input_shaper_backups')
PRINTER_CONFIG = Path('/home/pi/printer_data/config/printer.cfg')
PLOTTER = Path('/home/pi/klipper/scripts/calibrate_shaper.py')
CSV_TEMP = Path('/tmp')
SENSORS = {'adxl345', 'lis2dw', 'lis3dh', 'mpu9250', 'icm20948', 'bmi160', 'bmi088'}
SHAPERS = {'zv', 'mzv', 'zvd', 'ei', '2hump_ei', '3hump_ei'}
RUN_ID = re.compile(r'\d{8}T\d{6}Z_[a-zA-Z0-9_-]+_[0-9a-f]{8}')


def moonraker(path, body=None, timeout=10):
    request = Request('http://127.0.0.1:7125' + path,
                      data=None if body is None else json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if 'error' in payload:
        raise ValueError(str(payload['error']))
    return payload['result']


def printer_state():
    info = moonraker('/printer/info')
    if info.get('state') != 'ready':
        raise ValueError('Klipper is not ready: ' + info.get('state_message', info.get('state', 'unknown')))
    return moonraker('/printer/objects/query?configfile&toolhead&print_stats&idle_timeout')['status']


def fingerprint(settings):
    relevant = {name: value for name, value in settings.items() if name != 'input_shaper'}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def capabilities(state):
    settings = state['configfile']['settings']
    sensors = sorted(name for name in settings if name.split()[0] in SENSORS)
    tester = settings.get('resonance_tester', {})
    axes = [axis for axis in ('x', 'y')
            if (tester.get('accel_chip_' + axis) or tester.get('accel_chip')) in sensors]
    return {'sensors': sensors, 'axes': axes,
            'resonance_tester': bool(tester), 'input_shaper': 'input_shaper' in settings,
            'current': settings.get('input_shaper', {}),
            'homed': state.get('toolhead', {}).get('homed_axes', ''),
            'print_state': state.get('print_stats', {}).get('state', 'unknown'),
            'fingerprint': fingerprint(settings)}


def require_idle(state, axes=()):
    available = capabilities(state)
    if available['print_state'] not in ('standby', 'complete', 'cancelled', 'error'):
        raise ValueError('Printer must be idle, not printing or paused.')
    if axes:
        if not set('xyz').issubset(available['homed']):
            raise ValueError('Home all axes before starting a resonance test.')
        if not set(axes).issubset(available['axes']):
            raise ValueError('Requested axis has no configured resonance-test accelerometer.')
    return available


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def run_path(identifier):
    if not RUN_ID.fullmatch(identifier):
        raise ValueError('Invalid run identifier.')
    path = DATA / identifier
    if path.is_symlink():
        raise ValueError('Invalid run directory.')
    return path


def acquire_lock():
    import fcntl
    DATA.mkdir(parents=True, exist_ok=True)
    handle = (DATA / '.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ValueError('An input-shaping operation is already running.')
    return handle


def parse_recommendation(output):
    match = re.search(r'Recommended shaper is (\w+) @ ([0-9.]+) Hz', output)
    if not match:
        raise ValueError('Klipper did not produce a shaper recommendation. See the analysis log.')
    recommendation = {'type': match[1], 'frequency': float(match[2])}
    validate_recommendations({'x': recommendation})
    return recommendation


def validate_recommendations(results):
    if not results or not set(results).issubset({'x', 'y'}):
        raise ValueError('No valid axis recommendations.')
    for result in results.values():
        frequency = float(result['frequency'])
        if result['type'] not in SHAPERS or not math.isfinite(frequency) or not 0 < frequency <= 300:
            raise ValueError('Invalid shaper recommendation.')


def update_config(original, results):
    validate_recommendations(results)
    newline = '\r\n' if '\r\n' in original else '\n'
    lines = original.splitlines(keepends=True)
    headers = [(index, re.match(r'^\s*\[([^\]]+)\]', line)) for index, line in enumerate(lines)]
    headers = [(index, match.group(1).strip()) for index, match in headers if match]
    sections = [index for index, name in headers if name == 'input_shaper']
    if len(sections) > 1:
        raise ValueError('Multiple [input_shaper] sections in standalone.cfg; resolve them before saving.')
    start = sections[0] if sections else len(lines)
    end = next((index for index, name in headers if index > start), len(lines))
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
    if sections:
        parser.read_string(''.join(lines[start:end]))
    else:
        parser.add_section('input_shaper')
    for axis, result in results.items():
        parser['input_shaper']['shaper_type_' + axis] = result['type']
        parser['input_shaper']['shaper_freq_' + axis] = str(result['frequency'])
    section = '[input_shaper]' + newline
    section += ''.join(f'{key}: {value}{newline}' for key, value in parser['input_shaper'].items()) + newline
    prefix = ''.join(lines[:start])
    if prefix and not prefix.endswith('\n'):
        prefix += newline
    return prefix + section + ''.join(lines[end:])


def launch(axis):
    if axis not in ('x', 'y', 'both'):
        raise ValueError('Axis must be x, y, or both.')
    axes = ['x', 'y'] if axis == 'both' else [axis]
    with acquire_lock() as lock:
        state = printer_state()
        available = require_idle(state, axes)
        if not PLOTTER.is_file():
            raise ValueError('Klipper calibrate_shaper.py is missing.')
        subprocess.run(['/usr/bin/python3', '-c', 'import numpy, matplotlib'], check=True,
                       capture_output=True, timeout=20)
        plot_help = subprocess.run(['/usr/bin/python3', str(PLOTTER), '--help'], check=True,
                       capture_output=True, text=True, timeout=20).stdout
        hostname = re.sub(r'[^a-zA-Z0-9_-]', '_', socket.gethostname())[:40]
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + hostname + '_' + uuid.uuid4().hex[:8]
        path = run_path(identifier)
        path.mkdir()
        record = {'id': identifier, 'created': datetime.now(timezone.utc).isoformat(),
                  'host': hostname, 'axes': axes, 'status': 'queued', 'message': 'Preparing test',
                  'fingerprint': available['fingerprint'], 'sensors': available['sensors'],
                  'max_smoothing': state['configfile']['settings']['resonance_tester'].get('max_smoothing'),
                  'square_corner_velocity': state['toolhead'].get('square_corner_velocity', 5),
                  'supports_scv': '--scv' in plot_help, 'results': {}, 'artifacts': {}}
        atomic_json(path / 'run.json', record)
        with (path / 'worker.log').open('a') as output:
            try:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', identifier],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 start_new_session=True, pass_fds=(lock.fileno(),))
            except Exception:
                record.update(status='error', message='Unable to launch calibration worker.')
                atomic_json(path / 'run.json', record)
                raise
        return {'id': identifier}


def worker(identifier):
    path = run_path(identifier)
    record = json.loads((path / 'run.json').read_text(encoding='utf-8'))
    try:
        for axis in record['axes']:
            state = printer_state()
            available = require_idle(state, [axis])
            if available['fingerprint'] != record['fingerprint']:
                raise ValueError('Printer configuration changed during the run.')
            record.update(status='testing', message='Measuring ' + axis.upper() + ' axis')
            atomic_json(path / 'run.json', record)
            moonraker('/printer/gcode/script',
                      {'script': f'TEST_RESONANCES AXIS={axis.upper()} OUTPUT=resonances NAME={identifier}'},
                      timeout=1800)
            source = CSV_TEMP / f'resonances_{axis}_{identifier}.csv'
            csv_path = path / f'{axis}.csv'
            shutil.copyfile(source, csv_path)
            record['artifacts'][axis] = ['csv']
            record.update(status='plotting', message='Analyzing ' + axis.upper() + ' axis')
            atomic_json(path / 'run.json', record)
            command = ['/usr/bin/python3', str(PLOTTER), str(csv_path), '-o', str(path / f'{axis}.png')]
            if record['max_smoothing'] is not None:
                command.extend(['-s', str(record['max_smoothing'])])
            if record['supports_scv']:
                command.extend(['--scv', str(record['square_corner_velocity'])])
            analysis = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                      env={**os.environ, 'MPLBACKEND': 'Agg'})
            (path / f'{axis}.txt').write_text(analysis.stdout + analysis.stderr, encoding='utf-8')
            record['artifacts'][axis].append('txt')
            if analysis.returncode or not (path / f'{axis}.png').is_file():
                raise ValueError('Klipper plot generation failed. See the analysis log.')
            record['artifacts'][axis].append('png')
            record['results'][axis] = parse_recommendation(analysis.stdout)
            atomic_json(path / 'run.json', record)
        record.update(status='complete', message='Results ready; saved configuration is unchanged.')
    except Exception as error:
        record.update(status='error', message=str(error) + ' Verify the printer is stopped before retrying.')
        print(record['message'], flush=True)
    atomic_json(path / 'run.json', record)


def apply(identifier):
    with acquire_lock():
        path = run_path(identifier)
        record = json.loads((path / 'run.json').read_text(encoding='utf-8'))
        if record['status'] != 'complete':
            raise ValueError('Only a completed run can be applied.')
        state = printer_state()
        available = require_idle(state)
        if available['fingerprint'] != record['fingerprint']:
            raise ValueError('Printer configuration differs from this run. Run a new test before applying.')
        if state['configfile'].get('save_config_pending'):
            raise ValueError('Klipper has unsaved calibration changes. Save or discard those first.')
        if PRINTER_CONFIG.exists() and re.search(r'^#\*#\s*\[input_shaper\]', PRINTER_CONFIG.read_text(encoding='utf-8'), re.M):
            raise ValueError('printer.cfg contains SAVE_CONFIG input-shaper values. Move those values into standalone.cfg and restart Klipper before applying from this page.')
        if CONFIG.is_symlink():
            raise ValueError('standalone.cfg is a symlink; save settings manually.')
        original = CONFIG.read_bytes() if CONFIG.exists() else b''
        updated = update_config(original.decode('utf-8'), record['results'])
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / (identifier + '-' + uuid.uuid4().hex[:8] + '.cfg.bak')
        backup.write_bytes(original)
        descriptor, temporary = tempfile.mkstemp(dir=CONFIG.parent, prefix='.input-shaper-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            if CONFIG.exists():
                stat = CONFIG.stat()
                os.chmod(temporary, stat.st_mode)
                os.chown(temporary, stat.st_uid, stat.st_gid)
            else:
                import pwd
                owner = pwd.getpwnam('pi')
                os.chown(temporary, owner.pw_uid, owner.pw_gid)
                os.chmod(temporary, 0o644)
            if (CONFIG.read_bytes() if CONFIG.exists() else b'') != original:
                raise ValueError('standalone.cfg changed while saving; retry after reviewing it.')
            os.replace(temporary, CONFIG)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        record['applied'] = datetime.now(timezone.utc).isoformat()
        record['backup'] = backup.name
        atomic_json(path / 'run.json', record)
        try:
            moonraker('/printer/gcode/script', {'script': 'RESTART'})
        except Exception:
            return {'message': 'Settings saved and backed up. Restart response was lost; verify Klipper reconnects or restart it manually.'}
        return {'message': 'Recommendations saved and backed up. Klipper is restarting.'}


def status():
    busy = False
    try:
        with acquire_lock():
            pass
    except ValueError:
        busy = True
    runs = []
    for file in sorted(DATA.glob('*/run.json'), reverse=True):
        try:
            record = json.loads(file.read_text(encoding='utf-8'))
            record.pop('settings', None)
            if not busy and record['status'] in ('queued', 'testing', 'plotting'):
                record.update(status='interrupted', message='Worker stopped before completion. Verify printer state before retrying.')
            runs.append(record)
        except (OSError, ValueError, KeyError):
            continue
    try:
        available = capabilities(printer_state())
        error = None
    except Exception as exception:
        available, error = None, str(exception)
    return {'busy': busy, 'printer': available, 'error': error, 'runs': runs}


def cgi():
    try:
        if os.environ.get('REQUEST_METHOD', 'GET') == 'GET':
            result = status()
        elif os.environ.get('REQUEST_METHOD') == 'POST':
            length = int(os.environ.get('CONTENT_LENGTH', '0'))
            if not 0 < length <= 2048:
                raise ValueError('Invalid request size.')
            params = parse_qs(sys.stdin.read(length), strict_parsing=True)
            action = params.get('action', [''])[0]
            if action == 'start':
                result = launch(params.get('axis', [''])[0])
            elif action == 'apply':
                result = apply(params.get('id', [''])[0])
            elif action == 'stop':
                moonraker('/printer/emergency_stop', {})
                result = {'message': 'Emergency stop sent. Klipper must be restarted before further motion.'}
            else:
                raise ValueError('Unknown action.')
        else:
            raise ValueError('Use GET or POST.')
        print('Content-Type: application/json\nCache-Control: no-store\n')
        print(json.dumps(result))
    except Exception as error:
        print('Status: 400 Bad Request\nContent-Type: application/json\nCache-Control: no-store\n')
        print(json.dumps({'error': str(error)}))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(sys.argv[2])
    else:
        cgi()