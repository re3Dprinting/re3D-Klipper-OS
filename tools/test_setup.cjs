const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const vm = require('node:vm');

const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
const www = path.join(filesystem, 'opt', 'mconfig', 'www');
const scripts = path.join(filesystem, 'opt', 'custompios', 'scripts');
const bash = process.env.BASH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles, 'Git', 'bin', 'bash.exe') : 'bash');

function runShell(source, { step, flashing = false, body = '', method = 'POST' } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-setup-'));
  try {
    if (step) fs.writeFileSync(path.join(directory, 'step'), step);
    if (flashing) fs.writeFileSync(path.join(directory, 'flash'), '');
    fs.writeFileSync(path.join(directory, 'mode'), 'klipperscreen');
    fs.writeFileSync(path.join(directory, 'url'), 'http://printer.local/\n');
    const result = spawnSync(bash, ['-c', source
      .replaceAll('/etc/firstboot-splash', './flash')
      .replaceAll('/var/lib/re3d-setup/step', './step')
      .replaceAll('/etc/re3d-display-mode', './mode')
      .replaceAll('/boot/firmware/fullpageos.txt', './url')
      .replaceAll('/proc/cpuinfo', '/dev/null')
      .replaceAll('/bin/systemctl reboot >/dev/null 2>&1', 'echo reboot >> ./reboots')], {
      cwd: directory, input: body, encoding: 'utf8', timeout: 5000,
      env: { ...process.env, REQUEST_METHOD: method, CONTENT_LENGTH: String(body.length) }
    });
    assert.ifError(result.error);
    return {
      ...result,
      mode: fs.readFileSync(path.join(directory, 'mode'), 'utf8'),
      rebooted: fs.existsSync(path.join(directory, 'reboots'))
    };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('only unfinished setup redirects the kiosk URL; flashing keeps its existing path', () => {
  const source = fs.readFileSync(path.join(scripts, 'get_url'), 'utf8');
  for (const step of ['welcome', 'machine', 'network', 'display', 'finish']) {
    assert.equal(runShell(source, { step }).stdout.trim(), 'http://localhost:8081/?setup=1');
  }
  for (const options of [{}, { step: 'completed' }, { step: 'machine', flashing: true }]) {
    assert.equal(runShell(source, options).stdout.trim(), 'http://printer.local/');
  }
});

test('unfinished setup and firmware flashing override KlipperScreen without overwriting its mode', () => {
  const source = fs.readFileSync(path.join(scripts, 're3d-session'), 'utf8')
    .replace('exec >> "$LOG" 2>&1', ':')
    .replace('if [ "$MODE" = "klipperscreen" ]; then', 'echo "$MODE"; exit 0');
  for (const options of [{ step: 'display' }, { flashing: true }, { step: 'finish' }]) {
    const result = runShell(source, options);
    assert.equal(result.stdout.trim().split('\n').at(-1), 'mainsail');
    assert.equal(result.mode, 'klipperscreen');
  }
  for (const options of [{}, { step: 'completed' }]) {
    assert.equal(runShell(source, options).stdout.trim().split('\n').at(-1), 'klipperscreen');
  }
});

test('display settings can be deferred; ordinary System requests still reboot', () => {
  const source = fs.readFileSync(path.join(www, 'cgi-bin', 'set_display_mode.sh'), 'utf8');
  let result = runShell(source, { body: 'mode=mainsail&reboot=defer' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /200 OK/);
  assert.equal(result.mode, 'mainsail');
  assert.equal(result.rebooted, false);
  result = runShell(source, { body: 'mode=mainsail' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.rebooted, true);
  for (const options of [
    { body: 'mode=invalid' }, { body: 'mode=mainsail&reboot=invalid' }, { method: 'GET' }
  ]) {
    result = runShell(source, options);
    assert.match(result.stdout, /Status: (400|405)/);
    assert.equal(result.mode, 'klipperscreen');
    assert.equal(result.rebooted, false);
  }
});

test('machine save and reboot scheduling failures return HTTP errors instead of success', () => {
  const machine = fs.readFileSync(path.join(www, 'cgi-bin', 'set_machine_type.sh'), 'utf8')
    .replace('/usr/local/bin/set_machine_type', 'false');
  const result = runShell(machine, { body: 'type=Gigabot+4' });
  assert.match(result.stdout, /Status: 500 Internal Server Error/);
  assert.notEqual(result.status, 0);
  const reboot = fs.readFileSync(path.join(www, 'cgi-bin', 'reboot.sh'), 'utf8');
  for (const success of [true, false]) {
    const stub = `sudo() { ${success ? 'return 0' : 'echo denied; return 1'}; }\n`;
    const response = runShell(stub + reboot);
    if (success) {
      assert.equal(response.status, 0);
      assert.match(response.stdout, /Reboot scheduled/);
    } else {
      assert.match(response.stdout, /Status: 500 Internal Server Error/);
      assert.match(response.stdout, /denied/);
    }
  }
});

function element() {
  return {
    hidden: false, disabled: false, textContent: '', innerHTML: '', value: 'mainsail',
    dataset: {}, listeners: {}, checked: true,
    addEventListener(name, fn) { this.listeners[name] = fn; },
    querySelectorAll() { return []; },
    querySelector() { return this.radio || (this.radio = element()); },
    setAttribute() {}, removeAttribute() {}, focus() {}
  };
}

async function mount({ saved = 'welcome', failure = '', flashing = false } = {}) {
  const elements = new Map();
  const byId = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const state = { step: saved, failure, flashing };
  const requests = [];
  const loads = [];
  const events = {};
  const context = {
    location: { search: '?setup=1', hostname: 'printer.local', href: '' },
    URLSearchParams, console,
    window: { scrollTo() {}, addEventListener(name, fn) { events[name] = fn; } },
    document: { getElementById: byId, body: { classList: { add() {} } } },
    cleanupTabArtifacts() {}, currentTab: null,
    loadTab: async name => { loads.push(name); return true; },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (state.failure === url) throw new Error('Connection lost');
      if (url.endsWith('get_display_mode.sh')) return { ok: true, text: async () => 'mainsail' };
      if (url.endsWith('setup.py') && options.method === 'POST') {
        state.step = JSON.parse(options.body).step;
      }
      return {
        ok: state.httpFailure !== url, status: state.httpFailure === url ? 500 : 200,
        json: async () => state.httpFailure === url
          ? { error: 'Unable to save settings' } : { step: state.step, flashing: state.flashing }
      };
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(www, 'setup.js'), 'utf8'), context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle();
  async function click(id) { byId(id).listeners.click(); await settle(); }
  return { byId, state, requests, loads, events, context, click, settle };
}

test('wizard saves each step, requires a saved machine, defers display switching, and finishes with a reboot', async () => {
  const wizard = await mount();
  assert.equal(wizard.byId('setup-title').textContent, 'Welcome to your Gigabot');
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'machine');
  assert.equal(wizard.byId('setup-next').disabled, true);
  wizard.events['re3d-machine-result']({ detail: { ok: false } });
  assert.equal(wizard.byId('setup-next').disabled, true);
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  assert.equal(wizard.byId('setup-next').disabled, false);
  wizard.byId('page').listeners.change();
  assert.equal(wizard.byId('setup-next').disabled, true);
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'network');
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'display');
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'finish');
  const display = wizard.requests.find(r => r.url.endsWith('set_display_mode.sh'));
  assert.equal(display.options.body.get('reboot'), 'defer');
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'completed');
  assert.ok(wizard.requests.some(r => r.url.endsWith('reboot.sh')));
  assert.equal(wizard.byId('setup-next').disabled, true);
});

test('skip preserves progress; a new browser session resumes it, and Back persists', async () => {
  const wizard = await mount({ saved: 'network' });
  assert.equal(wizard.loads[0], 'network');
  await wizard.click('setup-skip');
  assert.equal(wizard.state.step, 'network');
  assert.equal(wizard.context.location.href, 'http://printer.local:80/');
  const resumed = await mount({ saved: wizard.state.step });
  await resumed.click('setup-back');
  assert.equal(resumed.state.step, 'machine');
});

test('display choices include screenshots, distinct descriptions, and accessible radio labels', async () => {
  const wizard = await mount({ saved: 'display' });
  const html = wizard.byId('page').innerHTML;
  assert.equal((html.match(/class="setup-preview"/g) || []).length, 2);
  assert.match(html, /alt="Mainsail dashboard/);
  assert.match(html, /alt="KlipperScreen home screen/);
  assert.doesNotMatch(html, /Screenshot coming soon/);
  assert.match(html, /Information-rich &amp; maximalist/);
  assert.match(html, /id="setup-mainsail-title">Mainsail \(default\)<\/strong>/);
  assert.match(html, /Minimalist &amp; snappy/);
  for (const [mode, width, height] of [['mainsail', 1154, 768], ['klipperscreen', 480, 320]]) {
    assert.match(html, new RegExp(`<img class="setup-preview" src="/img/setup-${mode}\\.png"`));
    const screenshot = fs.readFileSync(path.join(www, 'img', `setup-${mode}.png`));
    assert.equal(screenshot.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(screenshot.readUInt32BE(16), width);
    assert.equal(screenshot.readUInt32BE(20), height);
    assert.match(html, new RegExp(`value="${mode}"`));
    assert.match(html, new RegExp(`aria-labelledby="setup-${mode}-title"`));
    assert.match(html, new RegExp(`aria-describedby="setup-${mode}-description"`));
    assert.match(html, new RegExp(`id="setup-${mode}-title"`));
    assert.match(html, new RegExp(`id="setup-${mode}-description"`));
  }
});

test('manual launch starts setup on existing and completed installations', async () => {
  for (const saved of [null, 'completed']) {
    const wizard = await mount({ saved });
    assert.equal(wizard.state.step, 'welcome');
    assert.equal(wizard.byId('setup-next').disabled, false);
  }
});

test('persistence or display failures are visible and never advance progress', async () => {
  const wizard = await mount();
  wizard.state.failure = '/cgi-bin/setup.py';
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'welcome');
  assert.match(wizard.byId('setup-status').textContent, /Connection lost/);
  const display = await mount({ saved: 'display' });
  display.state.failure = '/cgi-bin/set_display_mode.sh';
  await display.click('setup-next');
  assert.equal(display.state.step, 'display');
  assert.match(display.byId('setup-status').textContent, /Connection lost/);
  display.state.failure = '';
  display.state.httpFailure = '/cgi-bin/set_display_mode.sh';
  await display.click('setup-next');
  assert.equal(display.state.step, 'display');
  assert.match(display.byId('setup-status').textContent, /Unable to save settings/);
});

test('setup cannot start while firmware flashing is active', async () => {
  const wizard = await mount({ flashing: true });
  assert.equal(wizard.byId('setup-next').disabled, true);
  assert.match(wizard.byId('setup-status').textContent, /flashing/);
  assert.equal(wizard.requests.length, 1);
});
