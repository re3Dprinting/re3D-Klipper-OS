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

test('navigation follows delayed connections and IP changes without overwriting offline or unchanged files', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-navi-'));
  try {
    const theme = path.join(directory, 'printer_data', 'config', '.theme');
    fs.mkdirSync(theme, { recursive: true });
    const navigation = path.join(theme, 'navi.json');
    const marker = path.join(theme, '.navi.last');
    const script = source.replace('USER_HOME="/home/pi"',
      `USER_HOME="${directory.replaceAll('\\', '/')}"`);
    function run(ethernet = '', wifi = '', other = '') {
      const mocks = `
ip() {
  local address=""
  case "$*" in
    *"dev eth0"*) address="$TEST_ETH" ;;
    *"dev wlan0"*) address="$TEST_WIFI" ;;
    *) address="$TEST_OTHER" ;;
  esac
  if [ -n "$address" ]; then
    printf '2: test inet %s/24 scope global test\\n' "$address"
  fi
}
chown() { :; }
`;
      const result = spawnSync(bash, ['-c', mocks + script], {
        encoding: 'utf8',
        env: { ...process.env, TEST_ETH: ethernet, TEST_WIFI: wifi, TEST_OTHER: other }
      });
      assert.equal(result.status, 0, result.stderr);
      return result.stdout;
    }
    run();
    assert.equal(fs.existsSync(navigation), false, 'offline first run must not create a loopback link');
    run('', '192.168.1.20');
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, 'http://192.168.1.20:8081/');

    const original = fs.readFileSync(navigation, 'utf8');
    fs.writeFileSync(marker, 'unchanged marker');
    fs.utimesSync(navigation, 946684800, 946684800);
    const modified = fs.statSync(navigation).mtimeMs;
    run('', '192.168.1.20');
    assert.equal(fs.statSync(navigation).mtimeMs, modified, 'unchanged link must not be rewritten');
    assert.equal(fs.readFileSync(marker, 'utf8'), 'unchanged marker');
    assert.match(run(), /leaving navi.json unchanged/);
    assert.equal(fs.readFileSync(navigation, 'utf8'), original);

    run('10.0.0.8', '192.168.1.20');
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, 'http://10.0.0.8:8081/');
    run('', '', '172.20.0.5');
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, 'http://172.20.0.5:8081/');
    run('169.254.1.2', '203.0.113.2');
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, 'http://172.20.0.5:8081/');

    fs.writeFileSync(navigation, '[{"href":"http://localhost:8081"}]');
    run('', '192.168.1.30');
    assert.equal(JSON.parse(fs.readFileSync(navigation))[0].href, 'http://192.168.1.30:8081/');
    assert.deepEqual(fs.readdirSync(theme).sort(), ['.navi.last', 'navi.json']);
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
