const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const source = fs.readFileSync(path.join(assets, 'kiosk.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));

function clientDocument() {
  const attributes = new Map();
  return {documentElement:{
    hasAttribute(name) { return attributes.has(name); },
    setAttribute(name, value) { attributes.set(name, value); },
    removeAttribute(name) { attributes.delete(name); }
  }};
}

async function fixture(search = '', mode = 'lockdown') {
  const elements = new Map();
  const listeners = {};
  const intervals = new Map();
  const calls = [];
  let now = 0;
  let session = false;
  let fail = false;
  let telemetry = {state:'printing', firmware:'ready', progress:.42,
    heaters:[{name:'extruder', temperature:205.2, target:210}, {name:'heater_bed', temperature:60, target:60}]};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value:'', hidden:false, textContent:'', src:'', contentWindow:{},
      addEventListener(event, callback) { this[event] = callback; }
    });
    return elements.get(id);
  }
  const context = {
    document:{getElementById:element},
    location:{search, replace(url) { calls.push(['redirect', url]); }},
    URLSearchParams,
    performance:{now:() => now},
    addEventListener(event, callback) { listeners[event] = callback; },
    setInterval(callback, delay) { intervals.set(delay, callback); },
    async fetch(url, options) {
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push([url, body]);
      if (fail) throw new Error('Network unavailable');
      let data = {};
      if (url === '/auth/status') data = {mode, touchscreen_locked:true};
      if (url === '/auth/printer-status') data = telemetry;
      if (url === '/auth/lock') session = false;
      if (url === '/auth/pin') session = body.pin === '1234';
      if (url === '/auth/kiosk-status') data = {unlocked:session, mode, touchscreen_locked:true};
      return {ok:true, json:async () => data};
    }
  };
  vm.runInNewContext(source, context);
  await flush();
  async function unlock() {
    element('pin').value = '1234';
    await element('form').submit({preventDefault() {}});
  }
  return {
    element, calls, intervals, listeners, unlock,
    setTelemetry(value) { telemetry = value; },
    setTime(value) { now = value; }, expire() { session = false; }, fail() { fail = true; }
  };
}

test('air-gapped Basic and Off modes use the shared lock and never redirect to unprotected control', async () => {
  for (const mode of ['basic', 'off']) {
    const f = await fixture('', mode);
    await f.unlock();
    await f.intervals.get(5000)();
    assert.equal(f.element('printer').src, '/');
    assert.equal(f.calls.some(([url]) => url === 'redirect'), false);
    await f.element('lock-now').click();
    assert.equal(f.element('printer').src, 'about:blank');
  }
});

test('startup and manual locking unload printer control, and PIN unlock does not reveal the PIN', async () => {
  const f = await fixture();
  assert.equal(f.element('printer').src, 'about:blank');
  assert.equal(f.element('screen').hidden, true);
  await f.unlock();
  assert.equal(f.element('printer').src, '/');
  assert.equal(f.element('pin').value, '');
  await f.element('lock-now').click();
  assert.equal(f.element('printer').src, 'about:blank');
  assert.equal(f.element('screen').hidden, true);
});

test('five-minute client idle deadline locks and background polling does not send activity', async () => {
  const f = await fixture();
  await f.unlock();
  await f.intervals.get(5000)();
  assert.equal(f.calls.filter(([url]) => url === '/auth/activity').length, 0);
  f.setTime(300000);
  f.intervals.get(1000)();
  await flush();
  assert.equal(f.element('printer').src, 'about:blank');
});

test('only messages from the embedded printer and its known origins extend activity', async () => {
  const f = await fixture();
  await f.unlock();
  const message = {data:{type:'re3d-activity'}, origin:'http://localhost',
    source:f.element('printer').contentWindow};
  f.listeners.message({...message, origin:'https://attacker.example'});
  f.listeners.message({...message, source:{}});
  await f.intervals.get(5000)();
  assert.equal(f.calls.filter(([url]) => url === '/auth/activity').length, 0);
  f.setTime(299000);
  f.listeners.message(message);
  assert.equal(f.calls.filter(([url]) => url === '/auth/activity').length, 1,
    'late activity must refresh immediately rather than wait beyond the server idle deadline');
  await f.intervals.get(5000)();
  assert.equal(f.calls.filter(([url]) => url === '/auth/activity').length, 1);
  f.setTime(300001);
  f.intervals.get(1000)();
  assert.equal(f.element('printer').src, '/');
});

test('server session expiry and network errors close the printer frame', async () => {
  for (const action of ['expire', 'fail']) {
    const f = await fixture();
    await f.unlock();
    f[action]();
    await f.intervals.get(5000)();
    assert.equal(f.element('printer').src, 'about:blank');
    assert.ok(f.element('status').textContent);
  }
});

test('activity hook does not treat timers as user input and redirects direct kiosk navigation', async () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const hooks = [];
  const messages = [];
  const context = {
    document:clientDocument(),
    fetch:async () => ({ok:true, json:async () => ({mode:'lockdown', local:true, touchscreen_locked:true})}),
    location:{pathname:'/', replace(url) { messages.push(url); }},
    top:{}, self:{}, parent:{postMessage(...args) { messages.push(args); }},
    addEventListener(event, callback) { hooks.push([event, callback]); }
  };
  vm.runInNewContext(client, context);
  await flush();
  assert.ok(hooks.some(([event]) => event === 'pointerdown'));
  assert.equal(messages.length, 0);
  hooks.find(([event]) => event === 'pointerdown')[1]();
  assert.equal(messages.length, 1);
  context.top = context.self;
  context.document = clientDocument();
  vm.runInNewContext(client, context);
  await flush();
  assert.equal(messages.at(-1), 'http://localhost/auth/kiosk');
  context.location.port = '8081';
  context.document = clientDocument();
  vm.runInNewContext(client, context);
  await flush();
  assert.equal(messages.at(-1), 'http://localhost/auth/kiosk?view=configurator');
});

test('Configurator navigation stays inside the unlocked wrapper and rejects untrusted requests', async () => {
  const f = await fixture();
  f.element('configurator').click();
  assert.equal(f.element('printer').src, 'about:blank');
  await f.unlock();
  const message = {source:f.element('printer').contentWindow,
    origin:'http://localhost', data:{type:'re3d-configurator'}};
  f.listeners.message({...message, origin:'https://attacker.example'});
  assert.equal(f.element('printer').src, '/');
  f.listeners.message({...message, source:{}});
  assert.equal(f.element('printer').src, '/');
  f.listeners.message(message);
  assert.equal(f.element('printer').src, 'http://localhost:8081/');
  f.element('printer-control').click();
  assert.equal(f.element('printer').src, '/');
  f.element('configurator').click();
  assert.equal(f.element('printer').src, 'http://localhost:8081/');
  await f.element('lock-now').click();
  assert.equal(f.element('printer').src, 'about:blank');
});

test('top-level Configurator handoff restores the intended page only after PIN unlock', async () => {
  const f = await fixture('?view=configurator');
  assert.equal(f.element('printer').src, 'about:blank');
  await f.unlock();
  assert.equal(f.element('printer').src, 'http://localhost:8081/');
  const invalid = await fixture('?view=https://attacker.example');
  await invalid.unlock();
  assert.equal(invalid.element('printer').src, '/');
});

test('the Mainsail Configurator link requests embedded navigation instead of replacing the kiosk', async () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const hooks = new Map();
  const messages = [];
  vm.runInNewContext(client, {
    document:clientDocument(),
    fetch:async () => ({ok:true, json:async () => ({mode:'lockdown', local:true, touchscreen_locked:true})}),
    location:{pathname:'/', href:'http://localhost/'}, URL,
    top:{}, self:{}, parent:{postMessage(...args) { messages.push(args); }},
    addEventListener(event, callback) { hooks.set(event, callback); }
  });
  await flush();
  let prevented = false;
  hooks.get('click')({target:{closest() { return {href:'http://localhost/configurator/'}; }},
    preventDefault() { prevented = true; }, stopImmediatePropagation() {}});
  assert.equal(prevented, true);
  assert.equal(messages[0][0].type, 're3d-configurator');
  assert.equal(messages[0][1], 'http://localhost');
});

test('Basic and Off bypass Mainsail service-worker navigation using the Configurator origin', async () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const cases = ['basic', 'off'].flatMap(mode =>
    ['http://localhost', 'https://localhost', 'http://127.0.0.1'].map(origin => ({mode, origin})));
  for (const {mode, origin} of cases) {
    const hooks = new Map();
    const redirects = [];
    vm.runInNewContext(client, {
      document:clientDocument(),
      fetch:async () => ({ok:true, json:async () => ({mode, local:true, touchscreen_locked:false})}),
      location:{pathname:'/', origin, href:origin + '/',
        assign(url) { redirects.push(url); }, replace() { assert.fail('Must not enter the PIN wrapper'); }},
      URL, addEventListener(event, callback) { hooks.set(event, callback); }
    });
    await flush();
    assert.deepEqual([...hooks.keys()], ['click']);
    for (const href of [origin + '/configurator/', origin + '/configurator/?setup=1#machine',
      'http://other-printer/configurator/', origin + '/files']) {
      let prevented = false;
      hooks.get('click')({target:{closest() { return {href}; }},
        preventDefault() { prevented = true; }, stopImmediatePropagation() {}});
      assert.equal(prevented, href.startsWith(origin + '/configurator/'));
    }
    const target = new URL(origin);
    target.port = target.protocol === 'https:' ? '8443' : '8081';
    assert.deepEqual(redirects, [target.href, target.href + '?setup=1#machine']);
    for (const redirect of redirects) {
      assert.notEqual(new URL(redirect).origin, origin,
        'The destination must escape the controlling Mainsail service worker');
    }
  }
});

test('extension and HTML injection share a document guard across isolated worlds', async () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const document = clientDocument();
  let checks = 0;
  const hooks = [];
  const context = {
    document, location:{pathname:'/'},
    fetch:async () => {
      checks++;
      return {ok:true, json:async () => ({mode:'basic', local:true, touchscreen_locked:false})};
    },
    addEventListener(event) { hooks.push(event); }
  };
  vm.runInNewContext(client, {...context});
  vm.runInNewContext(client, {...context});
  await flush();
  assert.equal(checks, 1);
  assert.deepEqual(hooks, ['click']);
});

test('air-gapped Basic and Off navigation retains the PIN wrapper and activity hooks', async () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  for (const mode of ['basic', 'off']) {
    const hooks = new Map();
    const messages = [];
    const self = {};
    const context = {
      document:clientDocument(),
      fetch:async () => ({ok:true, json:async () => ({mode, local:true, touchscreen_locked:true})}),
      location:{pathname:'/', href:'http://localhost/',
        replace(url) { messages.push(url); },
        assign() { assert.fail('Air-gap navigation must not bypass the PIN wrapper'); }},
      URL, top:self, self, parent:{postMessage(...args) { messages.push(args); }},
      addEventListener(event, callback) { hooks.set(event, callback); }
    };
    vm.runInNewContext(client, context);
    await flush();
    assert.deepEqual(messages, ['http://localhost/auth/kiosk']);
    messages.length = 0;
    context.top = {};
    context.document = clientDocument();
    vm.runInNewContext(client, context);
    await flush();
    hooks.get('pointerdown')();
    assert.equal(messages[0][0].type, 're3d-activity');
    let prevented = false;
    hooks.get('click')({target:{closest() { return {href:'http://localhost/configurator/'}; }},
      preventDefault() { prevented = true; }, stopImmediatePropagation() {}});
    assert.equal(prevented, true);
    assert.equal(messages[1][0].type, 're3d-configurator');
    assert.equal(messages[1][1], 'http://localhost');
  }
});

test('PIN layout uses the available screen and stretches the keypad on touchscreen displays', () => {
  const html = fs.readFileSync(path.join(assets, 'kiosk.html'), 'utf8');
  const css = fs.readFileSync(path.join(assets, 'security.css'), 'utf8');
  assert.match(html, /<body class="kiosk-page">/);
  assert.match(css, /\.kiosk-page \.pin-card \{[^}]*max-width: none;[^}]*min-height: calc\(100dvh - 24px\)/);
  assert.match(css, /\.kiosk-page #keys \{ grid-template-rows: repeat\(4, minmax\(48px,1fr\)\)/);
});

test('locked-screen telemetry shows print progress and heater temperatures without unlocking or activity', async () => {
  const f = await fixture();
  await flush();
  assert.equal(f.element('print-state').textContent, 'Printing - 42%');
  assert.match(f.element('heater-status').textContent, /Nozzle: 205.2 \/ 210/);
  assert.match(f.element('heater-status').textContent, /Bed: 60.0 \/ 60/);
  f.setTelemetry({state:'paused', firmware:'ready', progress:.5, heaters:[]});
  await f.intervals.get(5000)();
  await flush();
  assert.equal(f.element('print-state').textContent, 'Print paused - 50%');
  assert.equal(f.element('heater-status').textContent, 'No heaters configured.');
  assert.equal(f.element('printer').src, 'about:blank');
  assert.equal(f.calls.some(([url]) => url === '/auth/activity' || url === '/auth/pin'), false);
  f.fail();
  await f.intervals.get(5000)();
  await flush();
  assert.match(f.element('heater-status').textContent, /unavailable/);
  assert.doesNotMatch(f.element('heater-status').textContent, /205/);
});

test('emergency stop is immediate while locked, needs no PIN, and reports acknowledgement', async () => {
  const f = await fixture();
  const stop = f.element('emergency-stop').click();
  assert.equal(f.element('emergency-stop').disabled, true);
  await f.element('emergency-stop').click();
  await stop;
  assert.equal(f.calls.filter(([url]) => url === '/auth/emergency-stop').length, 1);
  assert.match(f.element('emergency-status').textContent, /acknowledged/);
  assert.equal(f.element('printer').src, 'about:blank');
  assert.equal(f.calls.some(([url]) => url === '/auth/pin'), false);
  assert.equal(f.element('emergency-stop').disabled, false);
});

test('emergency stop network failure never claims success and keeps retry available', async () => {
  const f = await fixture();
  f.fail();
  await f.element('emergency-stop').click();
  assert.match(f.element('emergency-status').textContent, /Stop NOT confirmed/);
  assert.match(f.element('emergency-status').textContent, /physical emergency stop/);
  assert.equal(f.element('emergency-stop').disabled, false);
});
