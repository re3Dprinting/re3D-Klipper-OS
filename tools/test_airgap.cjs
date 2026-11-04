const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const html = fs.readFileSync(path.join(assets, 'airgap.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const bash = process.env.BASH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles, 'Git', 'bin', 'bash.exe') : 'bash');

async function fixture(initial = {}, search = '') {
  const state = {enabled: false, applied: false, reboot_required: false, local: true,
    has_password: true, has_pin: true, security_mode: 'basic', ...initial};
  const elements = new Map();
  const calls = [];
  let failure = '';
  function el(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, hidden: false, disabled: true, textContent: '',
      addEventListener(event, callback) { this[event] = callback; }
    });
    return elements.get(id);
  }
  vm.runInNewContext(script, {
    document: {getElementById: el}, URLSearchParams, location: {search},
    async fetch(url, options = {}) {
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push({url, body});
      if (url === failure) return {ok: false, json: async () => ({error: 'Operation failed'})};
      if (url === '/auth/password') {
        state.has_password = true;
        if (body.pin) state.has_pin = true;
      }
      if (url === '/auth/airgap' && body) {
        state.enabled = body.enabled;
        state.reboot_required = state.enabled !== state.applied;
      }
      return {ok: true, json: async () => ({...state})};
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  return {el, calls, state, fail(url) { failure = url; },
    async save(enabled, password = 'administrator') {
      el('enabled').checked = enabled;
      el('network-enabled').checked = !enabled;
      el('enabled').change();
      el('password').value = password;
      await el('settings').submit({preventDefault() {}});
    }};
}

test('mode uses two visible, labelled radio choices instead of a native dropdown', async () => {
  assert.doesNotMatch(html, /<select\b/);
  assert.match(html, /role="radiogroup" aria-labelledby="mode-label"/);
  for (const id of ['enabled', 'network-enabled']) {
    assert.match(html, new RegExp(`<input id="${id}" type="radio" name="airgap-mode"`));
  }
  const f = await fixture();
  assert.equal(f.el('enabled').checked, false);
  assert.equal(f.el('network-enabled').checked, true);
  assert.equal(f.el('enabled').disabled, false);
  assert.equal(f.el('network-enabled').disabled, false);
  await f.save(true);
  assert.equal(f.el('enabled').checked, true);
  assert.equal(f.el('network-enabled').checked, false);
  await f.save(false);
  assert.deepEqual(f.calls.at(-1), {url: '/auth/airgap', body: {enabled: false, current: 'administrator'}});
});

test('air-gap settings save a strict boolean and password without claiming immediate isolation', async () => {
  const f = await fixture();
  await f.save(true);
  assert.deepEqual(f.calls.at(-1), {url: '/auth/airgap', body: {enabled: true, current: 'administrator'}});
  assert.equal(f.el('password').value, '');
  assert.match(f.el('message').textContent, /No isolation change takes effect until reboot/);
  assert.match(f.el('state').textContent, /not active.*reboot is required/);
  assert.equal(f.el('reboot').hidden, false);
  await f.el('reboot').click();
  assert.equal(f.calls.at(-1).url, '/auth/reboot');
});

test('first enable creates a confirmed administrator password before saving isolation', async () => {
  const f = await fixture({has_password: false}, '?setup=openbox');
  assert.equal(f.el('confirm-field').hidden, false);
  f.el('confirm').value = 'mismatch';
  await f.save(true);
  assert.equal(f.calls.length, 1);
  assert.match(f.el('message').textContent, /matching/);
  f.el('confirm').value = 'administrator';
  await f.save(true);
  assert.deepEqual(f.calls.slice(1).map(call => call.url), ['/auth/password', '/auth/airgap']);
  assert.equal(f.el('confirm').value, '');
  assert.equal(f.el('confirm-field').hidden, true);
  assert.equal(f.el('return').href, 'http://localhost:8081/?setup=1&flow=openbox');
});

test('a missing shared PIN must be created and confirmed before enabling isolation', async () => {
  const f = await fixture({has_pin: false});
  await f.save(true);
  assert.match(f.el('message').textContent, /matching touchscreen PINs/);
  assert.equal(f.calls.length, 1);
  assert.equal(f.el('pin-fields').hidden, false);
  f.el('pin').value = '1234';
  f.el('pin-confirm').value = '4321';
  await f.save(true);
  assert.equal(f.calls.length, 1);
  f.el('pin').value = '1234';
  f.el('pin-confirm').value = '1234';
  await f.save(true);
  assert.deepEqual(f.calls.slice(1).map(call => call.url), ['/auth/password', '/auth/airgap']);
  assert.deepEqual(f.calls[1].body, {current: 'administrator', mode: 'basic', pin: '1234'});
  assert.equal(f.el('pin').value, '');
  assert.equal(f.el('pin-confirm').value, '');
  assert.equal(f.el('pin-fields').hidden, true);
});

test('an existing Lockdown PIN is reused without a credential update', async () => {
  const f = await fixture({has_pin: true, security_mode: 'lockdown'});
  await f.save(true);
  assert.equal(f.el('pin-fields').hidden, true);
  assert.match(f.el('pin-help').textContent, /existing Lockdown touchscreen PIN/);
  assert.deepEqual(f.calls.map(call => call.url), ['/auth/airgap', '/auth/airgap']);
});

test('initial password and missing PIN are created together using the shared credential API', async () => {
  const f = await fixture({has_password: false, has_pin: false, security_mode: 'off'});
  f.el('confirm').value = 'administrator';
  f.el('pin').value = '5678';
  f.el('pin-confirm').value = '5678';
  await f.save(true);
  assert.deepEqual(f.calls[1].body, {new: 'administrator', mode: 'basic', pin: '5678'});
  assert.equal(f.calls.at(-1).url, '/auth/airgap');
});

test('disabling isolation does not require creating a missing PIN', async () => {
  const f = await fixture({has_pin: false, enabled: true, applied: true});
  await f.save(false);
  assert.equal(f.el('pin').required, false);
  assert.deepEqual(f.calls.map(call => call.url), ['/auth/airgap', '/auth/airgap']);
});

test('pending disable still reports isolation active and remote controls stay disabled', async () => {
  const f = await fixture({enabled: true, applied: true});
  await f.save(false);
  assert.match(f.el('state').textContent, /is active.*reboot is required/);
  const remote = await fixture({local: false});
  assert.equal(remote.el('enabled').disabled, true);
  assert.equal(remote.el('network-enabled').disabled, true);
  assert.equal(remote.el('save').disabled, true);
  assert.match(remote.el('password-help').textContent, /touchscreen/);
});

test('helper failure does not display a successful save or leak passwords', async () => {
  const f = await fixture();
  f.fail('/auth/airgap');
  await f.save(true);
  assert.equal(f.el('message').textContent, 'Operation failed');
  assert.equal(f.el('password').value, '');
  assert.equal(f.el('enabled').checked, true);
  assert.equal(f.el('network-enabled').checked, false);
  assert.equal(f.el('reboot').hidden, true);
});

test('failed initial status keeps controls disabled', async () => {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, {disabled: true, addEventListener() {}});
    return elements.get(id);
  };
  vm.runInNewContext(script, {document: {getElementById: el}, location: {search: ''}, URLSearchParams,
    fetch: async () => { throw new Error('Unavailable'); }});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(el('save').disabled, true);
  assert.equal(el('enabled').disabled, true);
  assert.equal(el('network-enabled').disabled, true);
  assert.match(el('state').textContent, /Unable to load air-gap settings/);
});

test('System does not probe GitHub when isolation is active, pending, or unverifiable', async () => {
  const system = fs.readFileSync(path.join(assets, 'www', 'tabs', 'system.html'), 'utf8');
  const source = system.slice(system.indexOf('async function checkUpdateConnectivity(){'),
    system.indexOf('async function checkForUpdates(manual){'));
  for (const isolation of [{enabled: true, applied: false}, {enabled: false, applied: true}, {}, null]) {
    const calls = [];
    const applied = [];
    const context = {
      updatePill: {}, btnUpdate: {}, btnCheckUpdate: {}, updateCard: {classList: {add() {}}},
      offlineHint: {}, outUpdate: {}, Date,
      setUpdatePill() {}, applyUpdateConnectivity(value) { applied.push(value); },
      async fetch(url) {
        calls.push(url);
        if (isolation === null) throw new Error('Status unavailable');
        return {ok: true, json: async () => isolation};
      }
    };
    await vm.runInNewContext(source + '\ncheckUpdateConnectivity()', context);
    assert.deepEqual(calls, ['/auth/airgap']);
    assert.deepEqual(applied, [false]);
  }
});

test('network CGI guards reject both pending enable and pending disable before doing work', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-airgap-'));
  try {
    const source = fs.readFileSync(path.join(assets, 'www', 'cgi-bin', '_cgi_common.sh'), 'utf8')
      .replaceAll('/etc/re3d-airgap/enabled', './enabled')
      .replaceAll('/run/re3d-airgap/enabled', './applied');
    for (const marker of ['enabled', 'applied']) {
      fs.writeFileSync(path.join(directory, marker), '');
      const result = spawnSync(bash, ['-c', source + '\nrequire_network\necho unexpected-network-work'], {
        cwd: directory, encoding: 'utf8', timeout: 5000
      });
      assert.ifError(result.error);
      assert.notEqual(result.status, 0);
      assert.match(result.stdout, /Status: 403 Forbidden/);
      assert.match(result.stdout, /air-gap policy/);
      assert.doesNotMatch(result.stdout, /unexpected-network-work/);
      fs.unlinkSync(path.join(directory, marker));
    }
    const connected = spawnSync(bash, ['-c', source + '\nrequire_network\necho permitted'], {
      cwd: directory, encoding: 'utf8', timeout: 5000
    });
    assert.equal(connected.status, 0);
    assert.equal(connected.stdout.trim(), 'permitted');
    for (const name of ['set_wifi', 'wifi_action', 'scan_wifi', 'check_os_update', 'check_update_connectivity', 'os_update']) {
      const cgi = fs.readFileSync(path.join(assets, 'www', 'cgi-bin', name + '.sh'), 'utf8');
      assert.match(cgi, /_cgi_common\.sh"\r?\nrequire_network/);
    }
  } finally {
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

test('air-gap display enforcement blocks KlipperScreen and preserves the saved preference', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-airgap-display-'));
  const filesystem = path.join(assets, '..', '..');
  const mapPaths = source => source
    .replaceAll('/etc/re3d-airgap/enabled', './enabled')
    .replaceAll('/run/re3d-airgap/enabled', './applied')
    .replaceAll('/etc/re3d-display-mode', './mode');
  try {
    fs.writeFileSync(path.join(directory, 'mode'), 'klipperscreen');
    const cgi = mapPaths(fs.readFileSync(path.join(assets, 'www', 'cgi-bin', 'set_display_mode.sh'), 'utf8'));
    const macro = mapPaths(fs.readFileSync(path.join(filesystem, 'home', 'pi', 'printer_data', 'config',
      'src', 'system_config', 'shell_commands', 'switch_display_mode.sh'), 'utf8'));
    const body = 'mode=klipperscreen&reboot=defer';
    for (const marker of ['enabled', 'applied']) {
      fs.writeFileSync(path.join(directory, marker), '');
      const response = spawnSync(bash, ['-c', cgi], {cwd: directory, input: body, encoding: 'utf8', timeout: 5000,
        env: {...process.env, REQUEST_METHOD: 'POST', CONTENT_LENGTH: String(body.length)}});
      assert.ifError(response.error);
      assert.notEqual(response.status, 0);
      assert.match(response.stdout, /409 Conflict/);
      assert.match(response.stdout, /PIN-protected Mainsail/);
      const command = spawnSync(bash, ['-c', macro, '--', 'klipperscreen'],
        {cwd: directory, encoding: 'utf8', timeout: 5000});
      assert.ifError(command.error);
      assert.notEqual(command.status, 0);
      assert.match(command.stderr, /PIN-protected Mainsail/);
      assert.equal(fs.readFileSync(path.join(directory, 'mode'), 'utf8'), 'klipperscreen');
      fs.unlinkSync(path.join(directory, marker));
    }
    fs.writeFileSync(path.join(directory, 'applied'), '');
    const session = mapPaths(fs.readFileSync(path.join(filesystem, 'opt', 'custompios', 'scripts', 're3d-session'), 'utf8'))
      .replace('exec >> "$LOG" 2>&1', ':')
      .replace('exec cage -d -- /opt/custompios/scripts/start_chromium_browser', 'echo PIN-KIOSK; exit 0');
    const result = spawnSync(bash, ['-c', session], {cwd: directory, encoding: 'utf8', timeout: 5000});
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /PIN-KIOSK/);
    assert.equal(fs.readFileSync(path.join(directory, 'mode'), 'utf8'), 'klipperscreen');
  } finally {
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

test('new HTML and modified shell entry points parse with the existing runtimes', () => {
  assert.doesNotThrow(() => new vm.Script(script));
  const filesystem = path.join(assets, '..', '..');
  const scripts = [
    path.join(assets, 'install_auth.sh'),
    ...['_cgi_common', 'set_wifi', 'wifi_action', 'scan_wifi', 'check_os_update', 'check_update_connectivity', 'os_update']
      .map(name => path.join(assets, 'www', 'cgi-bin', name + '.sh')),
    ...['re3d-os-update', 'flash_once.sh'].map(name =>
      path.join(filesystem, 'home', 'pi', 'printer_data', 'config', 'src', 'system_config', 'shell_commands', name)),
    path.join(__dirname, 're3d-os-update.sh')
  ];
  scripts.push(path.join(assets, 'www', 'cgi-bin', 'set_display_mode.sh'),
    ...['re3d-session', 'start_chromium_browser', 'start_klipperscreen']
      .map(name => path.join(filesystem, 'opt', 'custompios', 'scripts', name)),
    path.join(filesystem, 'home', 'pi', 'printer_data', 'config', 'src',
      'system_config', 'shell_commands', 'switch_display_mode.sh'));
  for (const filename of scripts) {
    const result = spawnSync(bash, ['-n'], {input: fs.readFileSync(filename), encoding: 'utf8', timeout: 5000});
    assert.ifError(result.error);
    assert.equal(result.status, 0, `${filename}: ${result.stderr}`);
  }
});
