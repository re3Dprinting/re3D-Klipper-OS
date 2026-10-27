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
    setAttribute() {}, removeAttribute() {}, focus() {}
  };
}

async function mount({ saved = 'machine', failure = '', flashing = false, openBox = false } = {}) {
  const elements = new Map();
  const byId = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  byId('setup-network-connect').value = 'connect';
  byId('setup-network-connect').checked = false;
  byId('setup-network-offline').value = 'offline';
  byId('setup-network-offline').checked = false;
  const state = { step: saved, failure, flashing };
  const requests = [];
  const loads = [];
  const events = {};
  const context = {
    location: { search: openBox ? '?setup=1&flow=openbox' : '?setup=1', hostname: 'printer.local', href: '' },
    URLSearchParams, console,
    window: { scrollTo() {}, addEventListener(name, fn) { events[name] = fn; } },
    document: { getElementById: byId, body: { classList: { add() {} } } },
    cleanupTabArtifacts() {}, currentTab: null,
    loadTab: async name => { loads.push(name); return true; },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (state.failure === url) throw new Error('Connection lost');
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
  return { byId, state, requests, loads, events, context, click, selectNetwork, settle };
}

async function mountDisplay() {
  const wizard = await mount();
  wizard.events['re3d-machine-result']({ detail: { ok: true } });
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
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
        await wizard.selectNetwork('offline');
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
  assert.match(index, /not network isolation/);
  assert.match(index, /not disabled or disconnected/);
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

test('offline setup avoids network controls and adapter actions; switching to Connect loads controls', async () => {
  const wizard = await mount();
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

test('open-box flow contains draft slides and network only, preserves settings, and reboots on completion', async () => {
  const wizard = await mount({ saved: 'openbox', openBox: true });
  assert.equal(wizard.byId('setup-title').textContent, 'Welcome to your Gigabot');
  assert.equal(wizard.byId('setup-eyebrow').textContent, 'OPEN-BOX SETUP');
  assert.equal(wizard.byId('setup-back').hidden, true);
  assert.match(wizard.byId('page').innerHTML, /Draft guide/);
  assert.match(wizard.byId('page').innerHTML, /picture coming soon/);
  assert.doesNotMatch(wizard.byId('setup-progress').innerHTML, /Machine|Display/);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Shipping stoppers and restraints');
  assert.match(wizard.byId('page').innerHTML, /Operations will provide the stopper locations/);
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Choose your network mode');
  assert.equal(wizard.byId('setup-next').disabled, true);
  await wizard.selectNetwork('connect');
  assert.deepEqual(wizard.loads, ['network']);
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  assert.equal(wizard.byId('setup-title').textContent, 'Ready to finish');
  assert.match(wizard.byId('page').innerHTML, /already configured by operations/);
  await wizard.click('setup-next');
  assert.equal(wizard.state.step, 'completed');
  assert.deepEqual(wizard.requests.map(r => r.url),
    ['/cgi-bin/setup.py', '/cgi-bin/setup.py', '/cgi-bin/setup.py', '/cgi-bin/reboot.sh']);
  const writes = wizard.requests.filter(r => r.options.method === 'POST' && r.url.endsWith('setup.py'));
  assert.deepEqual(writes.map(r => JSON.parse(r.options.body).step), ['openbox', 'completed']);
});

test('exiting unfinished open-box setup keeps it scheduled and reopening starts at the first slide', async () => {
  const wizard = await mount({ saved: 'completed', openBox: true });
  await wizard.click('setup-next');
  await wizard.click('setup-next');
  await wizard.selectNetwork('offline');
  await wizard.click('setup-next');
  await wizard.click('setup-skip');
  assert.equal(wizard.state.step, 'openbox');
  assert.equal(wizard.context.location.href, 'http://printer.local:80/');
  const reopened = await mount({ saved: wizard.state.step, openBox: true });
  assert.equal(reopened.byId('setup-title').textContent, 'Welcome to your Gigabot');
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
  const wizard = await mount({ saved: 'openbox', openBox: true });
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
