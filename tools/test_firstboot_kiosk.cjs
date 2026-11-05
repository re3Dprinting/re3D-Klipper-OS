const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const launcher = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
  'fullpageos', 'filesystem', 'opt', 'custompios', 'scripts', 'run_onepageos'), 'utf8');
const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
const browser = fs.readFileSync(path.join(filesystem,
  'opt', 'custompios', 'scripts', 'start_chromium_browser'), 'utf8');
const bash = process.env.BASH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles, 'Git', 'bin', 'bash.exe') : 'bash');

function launch({ firstboot = false, status = '503', disabled = false } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-kiosk-'));
  try {
    if (firstboot) fs.writeFileSync(path.join(directory, 'firstboot-splash'), '');
    fs.writeFileSync(path.join(directory, 'check_for_httpd'), disabled ? 'disabled\n' : 'enabled\n');
    const script = launcher
      .replaceAll('/etc/firstboot-splash', './firstboot-splash')
      .replaceAll('/boot/firmware/check_for_httpd', './check_for_httpd')
      .replaceAll('/opt/custompios/scripts/get_url', 'get_url')
      .replaceAll('%BROWSER_START_SCRIPT%', 'start_browser');
    const result = spawnSync(bash, ['-s'], {
      cwd: directory,
      input: `
swaybg() { :; }
get_url() { echo get_url >&2; echo http://localhost; }
curl() { echo curl >&2; echo '${status}'; }
start_browser() { echo browser >&2; }
sleep() { exit 0; }
${script}`,
      encoding: 'utf8',
      timeout: 3000
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    return result.stderr.trim().split(/\r?\n/).filter(Boolean);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('first boot launches the local splash without resolving or probing the web URL', () => {
  assert.deepEqual(launch({ firstboot: true }), ['browser']);
});

for (const status of ['200', '302', '401']) {
  test(`normal boot launches the browser for HTTP ${status}`, () => {
    assert.deepEqual(launch({ status }), ['get_url', 'curl', 'browser']);
  });
}

test('normal boot keeps waiting when the web server is unavailable', () => {
  assert.deepEqual(launch(), ['get_url', 'curl']);
});

test('normal boot still honors a disabled HTTP check', () => {
  assert.deepEqual(launch({ disabled: true }), ['get_url', 'curl', 'browser']);
});

function launchBrowser({ wayland = '', display = '', firstboot = true, attempts = 1, lockdown = false, airgap = false } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-browser-'));
  try {
    if (firstboot) fs.writeFileSync(path.join(directory, 'firstboot-splash'), '');
    if (lockdown) fs.writeFileSync(path.join(directory, 'lockdown'), '');
    if (airgap) fs.writeFileSync(path.join(directory, 'airgap'), '');
    fs.writeFileSync(path.join(directory, 'wait.html'), '<html></html>');
    const script = browser
      .replaceAll('/etc/re3d-security/lockdown', './lockdown')
      .replaceAll('/run/re3d-airgap/enabled', './airgap')
      .replaceAll('/etc/firstboot-splash', './firstboot-splash')
      .replaceAll('/tmp/', './')
      .replaceAll('/home/pi/.config/chromium-firstboot', './profile')
      .replaceAll('/home/pi/wait.html', './wait.html')
      .replaceAll('/opt/custompios/scripts/get_url', 'get_url');
    let result;
    for (let attempt = 0; attempt < attempts; attempt++) {
      result = spawnSync(bash, ['-s'], {
        cwd: directory,
        env: { ...process.env, WAYLAND_DISPLAY: wayland, DISPLAY: display,
          XDG_RUNTIME_DIR: directory.replaceAll('\\', '/') },
        input: `
chromium-browser() { printf '%s\\n' "$@" > browser-args; return 7; }
nohup() { "$@"; }
chown() { :; }
systemctl() { echo "$*" >> systemctl-actions; }
get_url() { echo http://localhost; }
${script}`,
        encoding: 'utf8',
        timeout: 3000
      });
      assert.ifError(result.error);
      assert.equal(result.status, wayland || display ? (lockdown || airgap ? 7 : 0) : 1, result.stderr);
    }
    const read = name => fs.existsSync(path.join(directory, name))
      ? fs.readFileSync(path.join(directory, name), 'utf8') : '';
    return {
      args: read('browser-args').trim().split(/\r?\n/).filter(Boolean),
      log: read('firstboot-kiosk.log'),
      serviceActions: read('systemctl-actions'),
      flagExists: fs.existsSync(path.join(directory, 'firstboot-splash')),
      markerExists: fs.existsSync(path.join(directory, 'firstboot-ui-started')),
      profiles: fs.readdirSync(directory).filter(name => name.startsWith('re3d-kiosk.'))
    };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('Lockdown launches the PIN wrapper with a fresh profile and preserves the touchscreen keyboard extension', () => {
  const result = launchBrowser({ wayland:'wayland-0', lockdown:true });
  assert.ok(result.args.includes('--app=http://localhost/auth/kiosk'));
  assert.ok(result.args.some(arg => arg.startsWith('--user-data-dir=')));
  assert.ok(result.args.includes('--load-extension=/home/pi/virtual_keyboard,/home/pi/re3d-kiosk-ui'));
  assert.ok(!result.args.includes('--incognito'));
  assert.deepEqual(result.profiles, []);
  assert.equal(result.markerExists, true);
});

test('air-gapped mode launches the same PIN wrapper and never the unprotected browser', () => {
  const result = launchBrowser({wayland:'wayland-0', firstboot:false, airgap:true});
  assert.ok(result.args.includes('--app=http://localhost/auth/kiosk'));
  assert.ok(result.args.some(arg => arg.startsWith('--user-data-dir=')));
  assert.deepEqual(result.profiles, []);
});

for (const firstboot of [true, false]) {
  test(`${firstboot ? 'first' : 'normal'} boot uses X11 in the legacy guisession`, () => {
    const result = launchBrowser({ display: ':0', firstboot });
    assert.ok(result.args.includes('--ozone-platform=x11'));
    assert.ok(!result.args.includes('--ozone-platform=wayland'));
    assert.ok(result.args.includes('--load-extension=/home/pi/virtual_keyboard,/home/pi/re3d-kiosk-ui'));
  });

  test(`${firstboot ? 'first' : 'normal'} boot prefers Wayland when XWayland is also available`, () => {
    const result = launchBrowser({ wayland: 'wayland-0', display: ':0', firstboot });
    assert.ok(result.args.includes('--ozone-platform=wayland'));
    assert.ok(!result.args.includes('--ozone-platform=x11'));
    assert.ok(result.args.includes('--load-extension=/home/pi/virtual_keyboard,/home/pi/re3d-kiosk-ui'));
  });
}

test('upload shortcut is hidden by a kiosk-only stylesheet, never the shared Mainsail theme', () => {
  const extension = path.join(filesystem, 'home', 'pi', 're3d-kiosk-ui');
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.content_scripts, [{
    matches: ['http://*/*', 'https://*/*'],
    css: ['mainsail.css'],
    run_at: 'document_start'
  }, {
    matches: ['http://localhost/*', 'https://localhost/*', 'http://127.0.0.1/*', 'https://127.0.0.1/*'],
    js: ['kiosk-client.js'],
    all_frames: true,
    run_at: 'document_start'
  }]);
  assert.equal(manifest.permissions, undefined);
  const css = fs.readFileSync(path.join(extension, 'mainsail.css'), 'utf8');
  assert.match(css, /\.v-application \.topbar \.upload-and-start-button\s*\{\s*display: none !important;\s*\}/);
  assert.doesNotMatch(css, /emergency-button|save-config-button|nth-child|@media/);
  const source = path.join(filesystem, 'home', 'pi', 'printer_data', 'config', 'src');
  assert.doesNotMatch(fs.readFileSync(path.join(source, 'common', 'mainsail.css'), 'utf8'),
    /upload-and-start-button|re3d-kiosk-ui/);
  for (const deposition of ['fff', 'fgf']) {
    const defaults = JSON.parse(fs.readFileSync(path.join(source, deposition, '.theme', 'default.json'), 'utf8'));
    assert.equal(defaults.uiSettings.boolHideUploadAndPrintButton, false);
  }
  const legacy = fs.readFileSync(path.join(source, 'system_config', 'start_chromium_browser'), 'utf8');
  assert.equal((legacy.match(/--load-extension=\/home\/pi\/virtual_keyboard,\/home\/pi\/re3d-kiosk-ui/g) || []).length, 2);
  const updater = fs.readFileSync(path.join(__dirname, 're3d-os-update.sh'), 'utf8');
  assert.ok(updater.includes('"${SRC_KIOSK_UI}/manifest.json" "${SRC_KIOSK_UI}/mainsail.css" "${DST_KIOSK_UI}/"'));
  const installer = fs.readFileSync(path.join(filesystem, 'opt', 'mconfig', 'install_auth.sh'), 'utf8');
  assert.ok(installer.includes('install -m 0644 /opt/mconfig/kiosk-client.js /home/pi/re3d-kiosk-ui/kiosk-client.js'));
});

test('three immediate browser crashes never stop flashing or remove its flags', () => {
  const result = launchBrowser({ display: ':0', attempts: 3 });
  assert.equal(result.serviceActions, '');
  assert.equal(result.flagExists, true);
  assert.equal(result.markerExists, true);
  assert.equal((result.log.match(/code 7/g) || []).length, 3);
});

test('a missing display environment is logged explicitly without launching Chromium', () => {
  const result = launchBrowser();
  assert.deepEqual(result.args, []);
  assert.match(result.log, /no Wayland or X11 display/);
  assert.equal(result.flagExists, true);
  assert.equal(result.markerExists, false);
  assert.equal(result.serviceActions, '');
});

test('first-boot LightDM post-step restores the wrapper after CustomPiOS selects guisession', () => {
  const dropin = fs.readFileSync(path.join(filesystem, 'root_init', 'etc', 'systemd',
    'system', 'update_lightdm_conf.service.d', 're3d-kiosk.conf'), 'utf8');
  const command = dropin.match(/^ExecStartPost=(.+)$/m)[1];
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-lightdm-'));
  try {
    const config = path.join(directory, 'lightdm.conf');
    fs.writeFileSync(config, '[Seat:*]\nuser-session=guisession\nautologin-session=guisession\nautologin-user=pi\n');
    const result = spawnSync(bash, ['-c',
      command.replace('/etc/lightdm/lightdm.conf', './lightdm.conf')], {
      cwd: directory, encoding: 'utf8', timeout: 3000
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    const updated = fs.readFileSync(config, 'utf8');
    assert.match(updated, /^user-session=re3d-kiosk$/m);
    assert.match(updated, /^autologin-session=re3d-kiosk$/m);
    assert.match(updated, /^autologin-user=pi$/m);
    assert.doesNotMatch(updated, /guisession/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
