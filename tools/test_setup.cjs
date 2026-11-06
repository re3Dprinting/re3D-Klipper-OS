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

test('wizard loader uses a fresh script URL on each visit and reports load failures', () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  const loader = index.match(/<script id="setup-loader">([\s\S]*?)<\/script>/)[1];
  assert.doesNotMatch(index, /<script src="setup\.js"/);
  const urls = [];
  for (const now of [100, 200]) {
    const scripts = [];
    const status = { hidden: true, textContent: '' };
    const context = {
      location: { search: '?setup=1' }, URLSearchParams, Date: { now: () => now },
      document: {
        createElement: () => ({}), getElementById: () => status,
        body: { appendChild: script => scripts.push(script) }
      }
    };
    vm.runInNewContext(loader, context);
    assert.equal(scripts.length, 1);
    urls.push(scripts[0].src);
    scripts[0].onerror();
    assert.equal(status.hidden, false);
    assert.match(status.textContent, /Unable to load setup/);
    context.location.search = '';
    vm.runInNewContext(loader, context);
    assert.equal(scripts.length, 1);
  }
  assert.deepEqual(urls, ['setup.js?cachebust=100', 'setup.js?cachebust=200']);
});

test('wizard labels its exit action and suppresses only the non-interactive heading outline', () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  assert.match(index, /id="setup-skip">Exit wizard<\/button>/);
  assert.doesNotMatch(index, /Skip for now/);
  assert.match(index, /\.setup-header h1:focus\s*\{\s*outline:\s*none\s*\}/);
  assert.match(index, /<h1 id="setup-title" tabindex="-1">/);
  assert.match(index, /\.setup-choices label:has\(input:focus-visible\)/);
});

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
  assert.equal(runShell(source, { step: 'openbox' }).stdout.trim(),
    'http://localhost:8081/?setup=1&flow=openbox');
  for (const options of [{}, { step: 'completed' }, { step: 'machine', flashing: true },
    { step: 'openbox', flashing: true }]) {
    assert.equal(runShell(source, options).stdout.trim(), 'http://printer.local/');
  }
});

test('unfinished setup and firmware flashing override KlipperScreen without overwriting its mode', () => {
  const source = fs.readFileSync(path.join(scripts, 're3d-session'), 'utf8')
    .replace('exec >> "$LOG" 2>&1', ':')
    .replace('if [ "$MODE" = "klipperscreen" ]; then', 'echo "$MODE"; exit 0');
  for (const options of [{ step: 'display' }, { flashing: true }, { step: 'finish' }, { step: 'openbox' }]) {
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
    prepend(...nodes) { this.prepended = nodes; },
    append(...nodes) { this.appended = nodes; },
    insertBefore(node) { (this.inserted || (this.inserted = [])).push(node); },
    setAttribute() {}, removeAttribute() {}, focus() {}
  };
}

async function mount({ saved = 'machine', failure = '', flashing = false, openBox = false, legacyPreview = false,
  machineType = 'Gigabot 4', machineStatus = 200, airgap = {enabled: false, applied: false} } = {}) {
  const elements = new Map();
  const byId = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  byId('setup-network-connect').value = 'connect';
  byId('setup-network-connect').checked = false;
  byId('setup-network-offline').value = 'offline';
  byId('setup-network-offline').checked = false;
  const state = { step: saved, failure, flashing, airgap };
  byId('page').parentElement = element();
  const bodyClasses = new Set();
  const requests = [];
  const loads = [];
  const events = {};
  const context = {
    location: {
      search: (openBox ? '?setup=1&flow=openbox' : '?setup=1') + (legacyPreview ? '&preview=1' : ''),
      hostname: 'printer.local', href: '',
      replace(href) { this.href = href; this.replaced = true; }
    },
    URLSearchParams, console,
    window: { scrollTo() {}, addEventListener(name, fn) { events[name] = fn; } },
    document: { getElementById: byId, createElement: element, body: { classList: {
      add(name) { bodyClasses.add(name); }, remove(name) { bodyClasses.delete(name); }
    } } },
    cleanupTabArtifacts() {}, currentTab: null,
    loadTab: async name => { loads.push(name); return true; },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (state.failure === url) throw new Error('Connection lost');
      if (url === '/auth/airgap') return {ok: true, json: async () => state.airgap};
      if (url.endsWith('get_machine_type.sh')) {
        return {ok: machineStatus === 200, status: machineStatus, text: async () => machineType};
      }
      if (url.endsWith('get_display_mode.sh')) return { ok: true, text: async () => 'mainsail' };
      if (url.endsWith('setup.py') && options.method === 'POST' && state.httpFailure !== url) {
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
  async function selectNetwork(mode) {
    for (const value of ['connect', 'offline']) {
      byId(`setup-network-${value}`).checked = value === mode;
    }
    await byId(`setup-network-${mode}`).listeners.change();
    await settle();
  }
  return { byId, state, requests, loads, events, context, click, selectNetwork, settle, bodyClasses };
}

async function mountDisplay() {
  const wizard = await mount();
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  await wizard.click('setup-next');
  await wizard.selectNetwork('connect');
  await wizard.click('setup-next');
  return wizard;
}

test('wizard keeps step navigation in memory, requires a saved machine, and persists completion', async () => {
  const wizard = await mount();
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your machine');
  assert.deepEqual(wizard.loads, ['machine']);
  assert.equal(wizard.byId('setup-back').hidden, true);
  assert.match(wizard.byId('setup-progress').innerHTML, /1\. Machine/);
  assert.match(wizard.byId('setup-progress').innerHTML, /4\. Finish/);
  assert.doesNotMatch(wizard.byId('setup-progress').innerHTML, /welcome/i);
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
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
  assert.equal(wizard.state.step, 'machine');
  assert.equal(wizard.byId('setup-next').disabled, true);
  await wizard.selectNetwork('connect');
  assert.deepEqual(wizard.loads, ['machine', 'network']);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your display');
  assert.equal(wizard.state.step, 'machine');
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Ready to finish');
  assert.equal(wizard.state.step, 'machine');
  const display = wizard.requests.find(r => r.url.endsWith('set_display_mode.sh'));
  assert.equal(display.options.body.get('reboot'), 'defer');
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'completed');
  assert.ok(wizard.requests.some(r => r.url.endsWith('reboot.sh')));
  assert.equal(wizard.byId('setup-next').disabled, true);
  const writes = wizard.requests.filter(r => r.url.endsWith('setup.py') && r.options.method === 'POST');
  assert.deepEqual(writes.map(r => JSON.parse(r.options.body).step), ['machine', 'completed']);
});

test('exiting or reloading at any unfinished step starts over at Machine', async () => {
  for (const advances of [1, 2, 3]) {
    const wizard = await mount();
    wizard.events['re3d-machine-result']({ detail: { ok: true } });
    for (let count = 0; count < advances; count++) {
      if (wizard.byId('setup-title').textContent === 'Choose your network mode') {
        await wizard.selectNetwork('connect');
      }
      await wizard.click('setup-next');
    }
    assert.notEqual(wizard.byId('setup-title').textContent, 'Choose your machine');
    await wizard.click('setup-skip');
    assert.equal(wizard.state.step, 'machine');
    assert.equal(wizard.context.location.href, 'http://printer.local:80/');
    const reopened = await mount({ saved: wizard.state.step });
    assert.deepEqual(reopened.loads, ['machine']);
    assert.equal(reopened.byId('setup-back').hidden, true);
    assert.equal(reopened.byId('setup-next').disabled, true);
    reopened.events['re3d-machine-result']({ detail: { ok: true } });
    await reopened.click('setup-next');
    assert.equal(reopened.byId('setup-next').disabled, true);
    assert.equal(reopened.byId('setup-network-offline').checked, false);
    assert.equal(reopened.byId('setup-network-connect').checked, false);
  }
});

test('forward and Back navigation do not write step progress', async () => {
  const wizard = await mountDisplay();
  wizard.state.failure = '/cgi-bin/setup.py';
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Ready to finish');
  await wizard.click('setup-back');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your display');
  await wizard.click('setup-back');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
  await wizard.click('setup-back');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your machine');
  assert.equal(wizard.byId('setup-back').hidden, true);
  assert.equal(wizard.byId('setup-status').textContent, '');
  const writes = wizard.requests.filter(r => r.url.endsWith('setup.py') && r.options.method === 'POST');
  assert.equal(writes.length, 1);
});

test('display choices include screenshots, distinct descriptions, and accessible radio labels', async () => {
  const wizard = await mountDisplay();
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

test('network choices appear only in the wizard and require a selection before continuing', async () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  assert.match(index, /id="setup-network-choice" class="card" hidden/);
  assert.match(index, /Connect to network/);
  assert.match(index, /Air-gapped mode/);
  assert.match(index, /Isolation takes effect only after reboot/);
  assert.match(index, /enter or create the shared PIN/);
  assert.match(index, /No administrator password is required/);
  const wizard = await mount();
  assert.equal(wizard.byId('setup-network-choice').hidden, true);
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-network-choice').hidden, false);
  assert.equal(wizard.byId('setup-next').disabled, true);
  assert.deepEqual(wizard.loads, ['machine']);
  await wizard.click('setup-next');
  assert.match(wizard.byId('setup-status').textContent, /Choose a network mode/);
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
});

test('air-gapped setup requires a saved policy without invoking adapter actions', async () => {
  const wizard = await mount({airgap: {enabled: true, applied: false}});
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  assert.equal(wizard.byId('setup-next').disabled, false);
  assert.equal(wizard.byId('setup-network-offline-note').hidden, false);
  assert.deepEqual(wizard.loads, ['machine']);
  assert.equal(wizard.byId('page').innerHTML, '');
  assert.ok(wizard.requests.every(request => request.url === '/cgi-bin/setup.py'));
  await wizard.selectNetwork('connect');
  assert.deepEqual(wizard.loads, ['machine', 'network']);
  assert.equal(wizard.byId('setup-network-offline-note').hidden, true);
  await wizard.selectNetwork('offline');
  assert.equal(wizard.byId('page').innerHTML, '');
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your display');
  assert.equal(wizard.byId('setup-network-choice').hidden, true);
  await wizard.click('setup-back');
  assert.equal(wizard.byId('setup-network-offline-note').hidden, false);
  assert.equal(wizard.byId('setup-next').disabled, false);
  assert.deepEqual(wizard.loads, ['machine', 'network']);
});

test('choosing air-gap does not pretend isolation was saved, and Connect cannot bypass active isolation', async () => {
  const wizard = await mount();
  wizard.events['re3d-machine-result']({detail: {ok: true}});
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  assert.match(wizard.byId('setup-status').textContent, /Open Air-gap settings/);
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
  wizard.state.airgap = {enabled: false, applied: true};
  await wizard.selectNetwork('connect');
  await wizard.click('setup-next');
  assert.match(wizard.byId('setup-status').textContent, /disable it and reboot/);
  wizard.state.failure = '/auth/airgap';
  await wizard.click('setup-next');
  assert.match(wizard.byId('setup-status').textContent, /Connection lost/);
});

test('air-gapped display step forces PIN-protected Mainsail without overwriting the saved preference', async () => {
  const wizard = await mount({airgap: {enabled: true, applied: false}});
  wizard.events['re3d-machine-result']({detail: {ok: true}});
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your display');
  assert.equal(wizard.byId('setup-status').textContent, '');
  assert.match(wizard.byId('page').appended[0].textContent, /PIN-protected Mainsail/);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Ready to finish');
  assert.equal(wizard.requests.some(request => request.url.includes('set_display_mode.sh')), false);
});

test('manual launch and all previously saved steps start at Machine', async () => {
  for (const saved of [null, 'completed', 'welcome', 'machine', 'network', 'display', 'finish']) {
    const wizard = await mount({ saved });
    assert.equal(wizard.state.step, 'machine');
    assert.deepEqual(wizard.loads, ['machine']);
    assert.equal(wizard.byId('setup-back').hidden, true);
    assert.equal(wizard.byId('setup-next').disabled, true);
    assert.equal(wizard.byId('setup-skip').disabled, false);
  }
});

test('setup status or display save failures are visible and never report completion', async () => {
  const initial = await mount({ failure: '/cgi-bin/setup.py' });
  assert.equal(initial.byId('setup-next').disabled, true);
  assert.match(initial.byId('setup-status').textContent, /Connection lost/);
  const display = await mountDisplay();
  display.state.failure = '/cgi-bin/set_display_mode.sh';
  await display.click('setup-next');
  assert.equal(display.byId('setup-title').textContent, 'Choose your display');
  assert.equal(display.state.step, 'machine');
  assert.match(display.byId('setup-status').textContent, /Connection lost/);
  display.state.failure = '';
  display.state.httpFailure = '/cgi-bin/set_display_mode.sh';
  await display.click('setup-next');
  assert.equal(display.byId('setup-title').textContent, 'Choose your display');
  assert.match(display.byId('setup-status').textContent, /Unable to save settings/);
  display.state.httpFailure = '';
  await display.click('setup-next');
  display.state.failure = '/cgi-bin/setup.py';
  await display.click('setup-next');
  assert.equal(display.state.step, 'machine');
  assert.equal(display.byId('setup-title').textContent, 'Ready to finish');
  assert.match(display.byId('setup-status').textContent, /Connection lost/);
  assert.equal(display.requests.some(r => r.url.endsWith('reboot.sh')), false);
});

test('setup cannot start while firmware flashing is active', async () => {
  const wizard = await mount({ flashing: true });
  assert.equal(wizard.byId('setup-next').disabled, true);
  assert.match(wizard.byId('setup-status').textContent, /flashing/);
  assert.equal(wizard.requests.length, 1);
});

test('open-box flow has a dedicated welcome and one precautions page, preserves settings, and reboots', async () => {
  const wizard = await mount({ saved: 'openbox', openBox: true });
  assert.equal(wizard.byId('setup-title').textContent, 'Welcome to your Gigabot 4');
  assert.equal(wizard.byId('setup-eyebrow').textContent, 'OPEN-BOX SETUP');
  assert.equal(wizard.byId('setup-back').hidden, true);
  assert.match(wizard.byId('page').innerHTML, /openbox-welcome/);
  assert.match(wizard.byId('page').innerHTML, /Dream BIG\.<br><span>Print HUGE\.<\/span>/);
  assert.match(wizard.byId('page').innerHTML, /<img class="openbox-welcome-image" src="\/img\/openbox-gigabot-4\.png" width="768" height="768"/);
  assert.doesNotMatch(wizard.byId('page').innerHTML, /<svg/);
  assert.doesNotMatch(wizard.byId('page').innerHTML, /openbox-guide-qr/);
  assert.match(wizard.byId('setup-progress').innerHTML, /1\. Welcome/);
  assert.match(wizard.byId('setup-progress').innerHTML, /2\. Precautions/);
  assert.match(wizard.byId('setup-progress').innerHTML, /4\. Finish/);
  assert.equal((wizard.byId('setup-progress').innerHTML.match(/<li /g) || []).length, 4);
  assert.doesNotMatch(wizard.byId('setup-progress').innerHTML, /Machine|Display/);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'A few things before you start');
  assert.match(wizard.byId('page').innerHTML, /Remove all eight shipping restraints before any movement/);
  assert.match(wizard.byId('page').innerHTML, /above AND below/);
  assert.match(wizard.byId('page').innerHTML, /Bed locks: 8 mm wrench/);
  assert.match(wizard.byId('page').innerHTML, /Triangle braces: 4 mm hex driver/);
  assert.match(wizard.byId('page').innerHTML, /openbox-bed-locks\.jpg/);
  assert.match(wizard.byId('page').innerHTML, /within 24 hours/);
  assert.match(wizard.byId('page').innerHTML, /at least two people/);
  assert.match(wizard.byId('page').innerHTML, /src="\/img\/getting-started-qr\.png" width="147" height="147"/);
  assert.match(wizard.byId('page').innerHTML, /href="https:\/\/re3d\.zendesk\.com\/hc\/en-us\/articles\/360038324771-Gigabot-Getting-Started-Guides" target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(wizard.byId('page').innerHTML, /placeholder|coming soon|not scannable/);
  assert.match(wizard.byId('page').innerHTML, /Never bypass the ground pin/);
  assert.match(wizard.byId('page').innerHTML, /does not perform those procedures/);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
  assert.equal(wizard.byId('setup-next').disabled, true);
  await wizard.selectNetwork('connect');
  assert.deepEqual(wizard.loads, ['network']);
  await wizard.selectNetwork('offline');
  wizard.state.airgap = {enabled: true, applied: false};
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Ready to finish');
  assert.match(wizard.byId('page').innerHTML, /already configured by operations/);
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'completed');
  assert.deepEqual(wizard.requests.map(r => r.url),
    ['/cgi-bin/setup.py', '/cgi-bin/setup.py', '/cgi-bin/get_machine_type.sh', '/auth/airgap', '/cgi-bin/setup.py', '/cgi-bin/reboot.sh']);
  const writes = wizard.requests.filter(r => r.options.method === 'POST' && r.url.endsWith('setup.py'));
  assert.deepEqual(writes.map(r => JSON.parse(r.options.body).step), ['openbox', 'completed']);
});

test('welcome keeps its heading and live buttons on the full-screen card and restores them on exit', async () => {
  const wizard = await mount({openBox: true});
  const {byId, bodyClasses} = wizard;
  const card = byId('page').querySelector('.openbox-welcome');
  assert.equal(bodyClasses.has('openbox-welcome-screen'), true);
  assert.deepEqual(card.prepended, [byId('setup-header')]);
  assert.deepEqual(card.appended, [byId('setup-status'), byId('setup-actions')]);
  await wizard.click('setup-next');
  assert.equal(bodyClasses.has('openbox-welcome-screen'), false);
  assert.deepEqual(byId('page').parentElement.appended, [byId('setup-status'), byId('setup-actions')]);
  assert.equal(byId('page').parentElement.inserted.at(-1), byId('setup-header'));
  await wizard.click('setup-back');
  assert.equal(bodyClasses.has('openbox-welcome-screen'), true);
  await wizard.click('setup-skip');
  assert.equal(wizard.context.location.href, 'http://printer.local:80/');
  const normal = await mount();
  assert.equal(normal.bodyClasses.has('openbox-welcome-screen'), false);
  const failed = await mount({openBox: true, machineStatus: 500});
  assert.equal(failed.bodyClasses.has('openbox-welcome-screen'), false);
  assert.match(failed.byId('setup-status').textContent, /Unable to load/);
});

test('full-screen welcome artwork spans the content area while actions remain on their own row', () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  assert.match(index, /\.openbox-welcome-screen \.openbox-welcome-image \{ grid-column: 2; grid-row: 1 \/ 5; max-width: min\(100%, calc\(100dvh - 128px\), 600px\)/);
  assert.match(index, /\.openbox-welcome-screen \.setup-actions \{ grid-column: 1 \/ -1; grid-row: 5;/);
  assert.match(index, /\.openbox-welcome-screen \.openbox-welcome-image \{ grid-column: 1; grid-row: 3;/);
  assert.match(index, /\.openbox-welcome-screen \.setup-header \{ display: none \}/);
  const setup = fs.readFileSync(path.join(www, 'setup.js'), 'utf8');
  assert.match(setup, /id="openbox-welcome-title" tabindex="-1"/);
  assert.match(setup, /byId\(step === 'welcome' \? 'openbox-welcome-title' : 'setup-title'\)\.focus\(\)/);
});

test('compact landscape setup saves header space without affecting welcome or ordinary tabs', () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  const compact = index.split('@media (min-width: 600px) and (max-height: 700px) {')[1]
    ?.split('@media (max-height: 500px)')[0];
  assert.ok(compact, 'Touchscreen layout must be limited to short, landscape-sized screens');
  assert.match(compact, /body\.guided-setup:not\(\.openbox-welcome-screen\) \.pages \{ padding: 16px 24px \}/);
  assert.match(compact, /\.guided-setup:not\(\.openbox-welcome-screen\) \.setup-header \{\s*display: grid; grid-template-columns: auto 1fr auto;/);
  assert.match(compact, /\.guided-setup \.setup-progress \{ margin: 0;/);
  assert.match(compact, /\.guided-setup button \{ min-height: 44px \}/);
  assert.match(compact, /\.guided-setup \.tog \{ width: 44px; height: 44px;/);
  assert.match(compact, /\.guided-setup \.setup-status:empty \{ display: none \}/);
  assert.match(compact, /\.guided-setup #out-machine \{ grid-column: 1; grid-row: 3;/);
  assert.match(compact, /\.guided-setup \.setup-preview \{ height: 120px \}/);
  assert.match(compact, /\.guided-setup \.wifi-list \{ max-height: 112px \}/);
  assert.doesNotMatch(compact, /overflow:\s*hidden|position:\s*fixed/);
});

test('setup uses the updated brand palette and locally bundled Lato', () => {
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  assert.match(index, /--brand: #FFD400;/);
  assert.match(index, /--fg:\s+#F2F2F2;/);
  assert.match(index, /--muted: #bcbec0;/);
  assert.match(index, /font: 14px\/1\.5 Lato, Roboto, Arial, sans-serif/);
  assert.match(index, /src: url\("\/fonts\/Lato-Regular\.ttf"\) format\("truetype"\)/);
  assert.ok(fs.statSync(path.join(www, 'fonts', 'Lato-Regular.ttf')).size > 0);
  assert.match(fs.readFileSync(path.join(www, 'fonts', 'OFL.txt'), 'utf8'), /SIL OPEN FONT LICENSE Version 1\.1/);
  assert.match(index, /padding: 40px; gap: 12px 24px/);
  assert.match(index, /--card-highlight: radial-gradient\(ellipse at 90% 50%, rgba\(57,59,62,\.3\), transparent 65%\)/);
  assert.match(index, /border: none; background: var\(--card-highlight\), var\(--surface-1\)/);
});

test('welcome uses each configured machine and its local cropped artwork', async () => {
  const machines = [
    ['Gigabot 4', 'gigabot-4', 768, 768],
    ['Gigabot 4 XLT', 'gigabot-4-xlt', 768, 768],
    ['Terabot 4', 'terabot-4', 768, 768],
    ['GigabotX 2', 'gigabotx-2', 768, 768],
    ['GigabotX XLT 2', 'gigabotx-2-xlt', 768, 768],
    ['TerabotX 2', 'terabotx-2', 768, 768]
  ];
  const machineTab = fs.readFileSync(path.join(www, 'tabs', 'machine.html'), 'utf8');
  assert.deepEqual([...machineTab.matchAll(/data-type="([^"]+)"/g)].map(match => match[1]).sort(),
    machines.map(([name]) => name).sort());
  for (const [name, image, width, height] of machines) {
    const artwork = fs.readFileSync(path.join(www, 'img', `openbox-${image}.png`));
    assert.equal(artwork.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(artwork.readUInt32BE(16), width);
    assert.equal(artwork.readUInt32BE(20), height);
    assert.equal(artwork[25], 6,
      'All supplied welcome illustrations retain their RGBA transparency');
    const wizard = await mount({openBox: true, machineType: `${name}\n`});
    assert.equal(wizard.byId('setup-title').textContent, `Welcome to your ${name}`);
    assert.ok(wizard.byId('page').innerHTML.includes(`src="/img/openbox-${image}.png" width="${width}" height="${height}"`));
    assert.ok(wizard.byId('page').innerHTML.includes(`alt="${name} printer"`));
    assert.ok(wizard.byId('page').innerHTML.includes(`Your ${name} is configured.`));
    await wizard.click('setup-next');
    await wizard.click('setup-back');
    assert.equal(wizard.byId('setup-title').textContent, `Welcome to your ${name}`);
    assert.equal(wizard.requests.filter(r => r.url.endsWith('get_machine_type.sh')).length, 1);
  }
});

test('machine lookup failures and unknown names are explicit rather than showing the wrong printer', async () => {
  for (const options of [
    {failure: '/cgi-bin/get_machine_type.sh', error: /Connection lost/},
    {machineStatus: 500, error: /Unable to load the configured machine \(500\)/},
    {machineType: '', error: /Unsupported configured machine/},
    {machineType: '<img src=x onerror=alert(1)>', error: /Unsupported configured machine/}
  ]) {
    const wizard = await mount({openBox: true, ...options});
    assert.equal(wizard.byId('setup-next').disabled, true);
    assert.match(wizard.byId('setup-status').textContent, options.error);
    assert.doesNotMatch(wizard.byId('page').innerHTML, /<img/);
    assert.deepEqual(wizard.requests.map(r => [r.url, r.options.method || 'GET']),
      [['/cgi-bin/setup.py', 'GET'], ['/cgi-bin/setup.py', 'POST'], ['/cgi-bin/get_machine_type.sh', 'GET']]);
  }
});

test('exiting unfinished open-box setup keeps it scheduled and reopening starts at the first slide', async () => {
  const wizard = await mount({ saved: 'completed', openBox: true, airgap: {enabled: true, applied: true} });
  await wizard.click('setup-next');
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  await wizard.click('setup-skip');
  assert.equal(wizard.state.step, 'openbox');
  assert.equal(wizard.context.location.href, 'http://printer.local:80/');
  const reopened = await mount({ saved: wizard.state.step, openBox: true });
  assert.equal(reopened.byId('setup-title').textContent, 'Welcome to your Gigabot 4');
  assert.equal(reopened.byId('setup-back').hidden, true);
  assert.equal(reopened.byId('setup-network-offline').checked, false);
  assert.deepEqual(reopened.loads, []);
});

test('normal wizard cannot accidentally replace a scheduled open-box guide', async () => {
  const wizard = await mount({ saved: 'openbox' });
  assert.equal(wizard.state.step, 'openbox');
  assert.match(wizard.byId('setup-status').textContent, /Open-box setup is scheduled/);
  assert.equal(wizard.byId('setup-next').disabled, true);
  assert.equal(wizard.requests.length, 1);
});

test('open-box initialization and completion failures remain explicit and do not reboot', async () => {
  const failed = await mount({ openBox: true, failure: '/cgi-bin/setup.py' });
  assert.equal(failed.byId('setup-next').disabled, true);
  assert.match(failed.byId('setup-status').textContent, /Connection lost/);
  const flashing = await mount({ openBox: true, flashing: true });
  assert.equal(flashing.byId('setup-next').disabled, true);
  assert.match(flashing.byId('setup-status').textContent, /flashing/);
  const wizard = await mount({ saved: 'openbox', openBox: true, airgap: {enabled: true, applied: false} });
  await wizard.click('setup-next');
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  wizard.state.httpFailure = '/cgi-bin/setup.py';
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'openbox');
  assert.match(wizard.byId('setup-status').textContent, /Unable to save settings/);
  assert.equal(wizard.requests.some(r => r.url.endsWith('reboot.sh')), false);
});

test('precautions photo and scannable QR are local, and welcome motion is finite and optional', () => {
  const source = fs.readFileSync(path.join(www, 'setup.js'), 'utf8');
  const image = fs.readFileSync(path.join(www, 'img', 'openbox-bed-locks.jpg'));
  assert.equal(image.subarray(0, 2).toString('hex'), 'ffd8');
  const welcome = fs.readFileSync(path.join(www, 'img', 'openbox-gigabot-4.png'));
  assert.equal(welcome.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(welcome.readUInt32BE(16), 768);
  assert.equal(welcome.readUInt32BE(20), 768);
  assert.match(source, /\/img\/openbox-bed-locks\.jpg/);
  const qr = fs.readFileSync(path.join(www, 'img', 'getting-started-qr.png'));
  assert.equal(qr.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(qr.readUInt32BE(16), 147);
  assert.equal(qr.readUInt32BE(20), 147);
  assert.match(source, /\/img\/getting-started-qr\.png/);
  assert.match(source, /alt="QR code for Gigabot Getting Started Guides"/);
  assert.doesNotMatch(source, /Draft guide|Operations will provide|unpacking slides are placeholders/);
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  assert.match(index, /\.openbox-guide-qr \{[^}]*width: 147px; height: 147px; background: #fff/);
  assert.doesNotMatch(index + source, /openbox-qr-placeholder|QR placeholder is not scannable/);
  assert.match(index, /@keyframes openbox-arrive/);
  assert.match(index, /\.openbox-welcome-image \{[^}]*width: 100%; height: auto; object-fit: contain/);
  assert.match(index, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.openbox-welcome-copy[^}]+animation: none/);
  const welcomeStyles = index.match(/\.openbox-welcome \{[\s\S]*?\.openbox-slide \{/)[0];
  assert.doesNotMatch(welcomeStyles, /animation:[^;}\n]*infinite/);
  assert.doesNotMatch(welcomeStyles, /brightness\(/);
});

test('developer preview is removed and retired links return to System without setup side effects', async () => {
  const system = fs.readFileSync(path.join(www, 'tabs', 'system.html'), 'utf8');
  const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
  const source = fs.readFileSync(path.join(www, 'setup.js'), 'utf8');
  assert.doesNotMatch(system, /openbox-preview-link|Developer preview|preview=1/);
  assert.doesNotMatch(index, /setup-preview-notice|Developer preview/);
  assert.doesNotMatch(source, /previewNotice|Finish preview|page\.inert/);
  for (const openBox of [false, true]) {
    for (const saved of ['completed', 'openbox']) {
      const wizard = await mount({saved, openBox, legacyPreview: true});
      assert.equal(wizard.context.location.href, '/#system');
      assert.equal(wizard.context.location.replaced, true);
      assert.equal(wizard.state.step, saved);
      assert.deepEqual(wizard.requests, []);
      assert.deepEqual(wizard.loads, []);
      assert.equal(wizard.bodyClasses.size, 0);
    }
  }
});

test('System schedules open-box setup only after confirmation, without rebooting, and shows failures', async () => {
  const html = fs.readFileSync(path.join(www, 'tabs', 'system.html'), 'utf8');
  const source = html.match(/<script id="openbox-scheduler">([\s\S]*?)<\/script>/)[1];
  for (const failure of ['none', 'http', 'transport']) {
    const elements = new Map();
    const byId = id => {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    };
    const requests = [];
    vm.runInNewContext(source, {
      document: { getElementById: byId },
      fetch: async (url, options) => {
        requests.push({url, options});
        if (failure === 'transport') throw new Error('Offline');
        return {ok: failure === 'none', status: 500,
          json: async () => failure === 'http' ? {error: 'Disk full'} : {step: 'openbox'}};
      }
    });
    byId('btn-schedule-openbox').listeners.click();
    assert.equal(requests.length, 0);
    assert.equal(byId('openbox-modal').hidden, false);
    byId('openbox-cancel').listeners.click();
    assert.equal(byId('openbox-modal').hidden, true);
    assert.equal(requests.length, 0);
    byId('btn-schedule-openbox').listeners.click();
    await byId('openbox-schedule').listeners.click();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/cgi-bin/setup.py');
    assert.equal(JSON.parse(requests[0].options.body).step, 'openbox');
    if (failure === 'none') {
      assert.equal(byId('openbox-modal').hidden, true);
      assert.match(byId('openbox-schedule-result').textContent, /has not been rebooted/);
    } else {
      assert.equal(byId('openbox-modal').hidden, false);
      assert.match(byId('openbox-schedule-status').textContent, /Unable to schedule/);
      assert.equal(byId('openbox-schedule-result').textContent, '');
    }
    assert.equal(byId('openbox-schedule').disabled, false);
    assert.equal(byId('openbox-cancel').disabled, false);
  }
});
