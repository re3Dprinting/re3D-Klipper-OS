const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
  'fullpageos', 'filesystem', 'opt', 'mconfig', 'www', 'tabs', 'system.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element() {
  return {
    children: [], dataset: {}, attributes: {}, listeners: {}, isConnected: true,
    textContent: '', disabled: false,
    setAttribute(name, value) { this.attributes[name] = value; },
    replaceChildren() { this.children = []; },
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); },
    addEventListener(name, callback) { this.listeners[name] = callback; }
  };
}

async function mount(checks = [{ label: 'CPU temperature', status: 'ok', detail: '45.0 C' }]) {
  const elements = Object.fromEntries(['refresh', 'summary', 'list']
    .map(name => [`sys-health-${name}`, element()]));
  const response = { ok: true, checks };
  const requests = [];
  vm.runInNewContext(script, {
    document: { getElementById: id => elements[id], createElement: element },
    AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (response.error) throw response.error;
      return { ok: response.ok, status: 503, json: async () => ({ checks: response.checks }) };
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  return { elements, requests, response };
}

test('health loads on entry, renders text safely, and refreshes without caching', async () => {
  const { elements, requests, response } = await mount();
  const list = elements['sys-health-list'];
  assert.equal(list.children.length, 1);
  assert.match(elements['sys-health-summary'].textContent, /All checks OK.*Last checked/);
  assert.equal(requests[0].url, '/cgi-bin/system_health.sh');
  assert.equal(requests[0].options.cache, 'no-store');
  response.checks = [{ label: '<b>Memory</b>', status: 'warning', detail: '<script>bad</script>' }];
  const pending = elements['sys-health-refresh'].listeners.click();
  assert.equal(elements['sys-health-refresh'].disabled, true);
  await pending;
  assert.equal(list.children.length, 1);
  const [heading, detail] = list.children[0].children;
  assert.equal(heading.children[0].textContent, '<b>Memory</b>');
  assert.equal(heading.children[1].dataset.status, 'warning');
  assert.equal(detail.textContent, '<script>bad</script>');
  assert.match(elements['sys-health-summary'].textContent, /1 need attention; 0 unavailable/);
  assert.equal(elements['sys-health-refresh'].disabled, false);
  assert.equal(list.attributes['aria-busy'], 'false');
});

test('unavailable checks never produce an all-healthy summary', async () => {
  const { elements } = await mount([
    { label: 'Power', status: 'unknown', detail: 'Unavailable: permission denied' },
    { label: 'Klipper', status: 'error', detail: 'failed (failed)' }
  ]);
  assert.match(elements['sys-health-summary'].textContent, /1 need attention; 1 unavailable/);
  assert.equal(elements['sys-health-list'].children[0].children[0].children[1].textContent, 'Unavailable');
});

test('HTTP, network, timeout and invalid data errors clear stale results and permit retry', async () => {
  for (const failure of [
    { ok: false }, { error: new Error('Offline') },
    { error: Object.assign(new Error(), { name: 'AbortError' }) },
    { checks: [] }, { checks: null }, { checks: [null] },
    { checks: [{ label: 'bad', status: 'constructor', detail: 'bad' }] }
  ]) {
    const { elements, response } = await mount();
    Object.assign(response, failure);
    await elements['sys-health-refresh'].listeners.click();
    assert.match(elements['sys-health-summary'].textContent, /Unable to load system health:/);
    assert.equal(elements['sys-health-list'].children.length, 0);
    assert.equal(elements['sys-health-refresh'].disabled, false);
  }
});

test('late responses do not render into a removed tab', async () => {
  const { elements } = await mount();
  elements['sys-health-list'].isConnected = false;
  await elements['sys-health-refresh'].listeners.click();
  assert.equal(elements['sys-health-list'].children.length, 0);
});
