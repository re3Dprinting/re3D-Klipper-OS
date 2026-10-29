const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const source = fs.readFileSync(path.join(assets, 'kiosk.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));

async function fixture() {
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
    location:{replace(url) { calls.push(['redirect', url]); }},
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
  hooks[0][1]();
  assert.equal(messages.length, 1);
  context.top = context.self;
  vm.runInNewContext(client, context);
  assert.equal(messages.at(-1), '/auth/kiosk');
});
