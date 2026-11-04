const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const source = fs.readFileSync(path.join(assets, 'kiosk.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));

async function fixture(search = '') {
  const elements = new Map();
  const listeners = {};
  const intervals = new Map();
  const calls = [];
  let now = 0;
  let session = false;
  let fail = false;
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
      if (url === '/auth/status') data = {mode:'lockdown'};
      if (url === '/auth/lock') session = false;
      if (url === '/auth/pin') session = body.pin === '1234';
      if (url === '/auth/kiosk-status') data = {unlocked:session};
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
    setTime(value) { now = value; }, expire() { session = false; }, fail() { fail = true; }
  };
}

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

test('activity hook does not treat timers as user input and redirects direct kiosk navigation', () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const hooks = [];
  const messages = [];
  const context = {
    location:{pathname:'/', replace(url) { messages.push(url); }},
    top:{}, self:{}, parent:{postMessage(...args) { messages.push(args); }},
    addEventListener(event, callback) { hooks.push([event, callback]); }
  };
  vm.runInNewContext(client, context);
  assert.ok(hooks.some(([event]) => event === 'pointerdown'));
  assert.equal(messages.length, 0);
  hooks.find(([event]) => event === 'pointerdown')[1]();
  assert.equal(messages.length, 1);
  context.top = context.self;
  vm.runInNewContext(client, context);
  assert.equal(messages.at(-1), 'http://localhost/auth/kiosk');
  context.location.port = '8081';
  vm.runInNewContext(client, context);
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

test('the Mainsail Configurator link requests embedded navigation instead of replacing the kiosk', () => {
  const client = fs.readFileSync(path.join(assets, 'kiosk-client.js'), 'utf8');
  const hooks = new Map();
  const messages = [];
  vm.runInNewContext(client, {
    location:{pathname:'/', href:'http://localhost/'}, URL,
    top:{}, self:{}, parent:{postMessage(...args) { messages.push(args); }},
    addEventListener(event, callback) { hooks.set(event, callback); }
  });
  let prevented = false;
  hooks.get('click')({target:{closest() { return {href:'http://localhost/configurator/'}; }},
    preventDefault() { prevented = true; }, stopImmediatePropagation() {}});
  assert.equal(prevented, true);
  assert.equal(messages[0][0].type, 're3d-configurator');
  assert.equal(messages[0][1], 'http://localhost');
});

test('PIN layout uses the available screen and stretches the keypad on touchscreen displays', () => {
  const html = fs.readFileSync(path.join(assets, 'kiosk.html'), 'utf8');
  const css = fs.readFileSync(path.join(assets, 'security.css'), 'utf8');
  assert.match(html, /<body class="kiosk-page">/);
  assert.match(css, /\.kiosk-page \.pin-card \{[^}]*max-width: none;[^}]*min-height: calc\(100dvh - 24px\)/);
  assert.match(css, /\.kiosk-page #keys \{ grid-template-rows: repeat\(4, minmax\(48px,1fr\)\)/);
});
