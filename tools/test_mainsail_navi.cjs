const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const filesystem = path.join(root, 'src', 'modules', 'fullpageos', 'filesystem');
const source = fs.readFileSync(path.join(filesystem, 'home', 'pi', 'printer_data',
  'config', 'src', 'system_config', 'shell_commands', 'update_mainsail_navi.sh'), 'utf8');
const bash = process.platform === 'win32' ? 'C:\\Program Files\\Git\\bin\\bash.exe' : 'bash';

test('navigation preserves the browser host and scheme, including the loopback kiosk', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-navi-'));
  try {
    const theme = path.join(directory, 'printer_data', 'config', '.theme');
    fs.mkdirSync(theme, { recursive: true });
    const navigation = path.join(theme, 'navi.json');
    const marker = path.join(theme, '.navi.last');
    const script = source.replace('USER_HOME="/home/pi"',
      `USER_HOME="${directory.replaceAll('\\', '/')}"`);
    function run() {
      const result = spawnSync(bash, ['-c', 'chown() { :; }\n' + script], { encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
    }
    run();
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, '/configurator/');
    fs.writeFileSync(marker, 'unchanged marker');
    fs.utimesSync(navigation, 946684800, 946684800);
    const modified = fs.statSync(navigation).mtimeMs;
    run();
    assert.equal(fs.statSync(navigation).mtimeMs, modified);
    assert.equal(fs.readFileSync(marker, 'utf8'), 'unchanged marker');
    for (const old of ['http://localhost:8081/', 'http://192.168.1.20:8081/']) {
      fs.writeFileSync(navigation, JSON.stringify([{ href: old }]));
      run();
      assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, '/configurator/');
    }
    assert.deepEqual(fs.readdirSync(theme).sort(), ['.navi.last', 'navi.json']);
    for (const type of ['fff', 'fgf']) {
      const bundled = JSON.parse(fs.readFileSync(path.join(filesystem, 'home', 'pi',
        'printer_data', 'config', 'src', type, '.theme', 'navi.json')));
      assert.equal(bundled[0].href, '/configurator/');
    }
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('timer is ordered, recurring, and deployed on new and existing images', () => {
  const units = path.join(filesystem, 'root_init', 'etc', 'systemd', 'system');
  const service = fs.readFileSync(path.join(units, 'update-mainsail-navi.service'), 'utf8');
  const timer = fs.readFileSync(path.join(units, 'update-mainsail-navi.timer'), 'utf8');
  assert.match(service, /After=.*klipper_config\.service/);
  assert.match(timer, /OnBootSec=5s/);
  assert.match(timer, /OnUnitInactiveSec=5s/);
  assert.match(timer, /Unit=update-mainsail-navi\.service/);
  assert.match(timer, /WantedBy=timers\.target/);
  const build = fs.readFileSync(path.join(root, 'src', 'modules', 'fullpageos', 'start_chroot_script'), 'utf8');
  const update = fs.readFileSync(path.join(root, 'tools', 're3d-os-update.sh'), 'utf8');
  assert.match(build, /systemctl enable update-mainsail-navi\.timer/);
  assert.ok(update.includes("-name '*.timer'"));
  assert.match(update, /systemctl enable --now update-mainsail-navi\.timer/);
});
