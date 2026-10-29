const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
  'fullpageos', 'filesystem', 'opt', 'mconfig', 'www', 'tabs', 'system.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

test('compact health panel spans both columns before system actions', () => {
  assert.match(html, /<div class="content sys-content">\s*<section class="card sys-health"/);
  assert.match(html, /\.sys-health\{[^}]*grid-column:1 \/ -1;[^}]*padding:12px 16px/);
  assert.match(html, /\.sys-health-list\{[^}]*grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/);
  for (const columns of [3, 2]) {
    assert.ok(html.includes(`grid-template-columns:repeat(${columns},minmax(0,1fr))`));
  }
  assert.match(html, /<details class="sys-health-help">\s*<summary>About these checks<\/summary>/);
  assert.equal((html.match(/id="sys-health-title"/g) || []).length, 1);
});

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
  const listeners = {};
  const timers = new Map();
  let now = 100000;
  let nextTimer = 0;
  let observer;
  const document = {
    hidden: false,
    getElementById: id => elements[id] || element(), createElement: element,
    addEventListener(name, callback) { listeners[name] = callback; },
    removeEventListener(name) { delete listeners[name]; }
  };
  function timer(callback, delay, repeat = false) {
    const id = ++nextTimer;
    timers.set(id, { callback, due: now + delay, repeat, delay });
    return id;
  }
  const flush = () => new Promise(resolve => setImmediate(resolve));
  vm.runInNewContext(script, {
    document, AbortController,
    Date: class extends Date { static now() { return now; } },
    setTimeout: timer, clearTimeout: id => timers.delete(id),
    setInterval: (callback, delay) => timer(callback, delay, true),
    clearInterval: id => timers.delete(id),
    MutationObserver: class {
      constructor(callback) { observer = this; this.callback = callback; }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (response.wait) {
        await new Promise((resolve, reject) => {
          response.release = resolve;
          options.signal.addEventListener('abort', () =>
            reject(Object.assign(new Error(), { name: 'AbortError' })), { once: true });
        });
      }
      if (response.error) throw response.error;
      return { ok: response.ok, status: 503, json: async () => ({ checks: response.checks }) };
    }
  });
  await flush();
  return {
    elements, requests, response, timers, listeners,
    async advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers.entries()].filter(([, value]) => value.due <= end)
          .sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        const [id, value] = next;
        now = value.due;
        if (value.repeat) value.due += value.delay;
        else timers.delete(id);
        value.callback();
        await flush();
      }
      now = end;
      await flush();
    },
    async visibility(hidden) {
      document.hidden = hidden;
      listeners.visibilitychange?.();
      await flush();
    },
    async removeTab() {
      elements['sys-health-list'].isConnected = false;
      observer.callback();
      await flush();
      assert.equal(observer.disconnected, true);
    }
  };
}

test('health loads on entry, renders text safely, and refreshes without caching', async () => {
  const { elements, requests, response } = await mount([
    { label: '<b>Memory</b>', status: 'ok', detail: '45.0 C' }
  ]);
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

test('HTTP, network, timeout and invalid data errors flag stale results and permit retry', async () => {
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
    assert.match(elements['sys-health-summary'].textContent, /Showing last readings \(stale\).*Retrying automatically/);
    assert.equal(elements['sys-health-list'].children.length, 1);
    assert.equal(elements['sys-health-refresh'].disabled, false);
  }
});

test('late responses do not render into a removed tab', async () => {
  const { elements } = await mount();
  elements['sys-health-list'].isConnected = false;
  await elements['sys-health-refresh'].listeners.click();
  assert.equal(elements['sys-health-list'].children.length, 1);
});

function allChecks() {
  return ['CPU temperature', 'CPU load', 'Memory', 'System storage (/)',
    'Printer storage', 'Uptime', 'Power & throttling', 'Klipper service', 'Moonraker service']
    .map(label => ({ label, status: 'ok', detail: `${label} initial` }));
}

test('live requests run every 5 seconds and full checks every 30 seconds, without replacing rows', async () => {
  const page = await mount(allChecks());
  const list = page.elements['sys-health-list'];
  const firstRow = list.children[0];
  page.response.checks = [{ label: 'CPU temperature', status: 'warning', detail: '76.0 C' }];
  await page.advance(4999);
  assert.equal(page.requests.length, 1);
  await page.advance(1);
  assert.equal(page.requests[1].url, '/cgi-bin/system_health.sh?scope=live');
  assert.equal(list.children.length, 9);
  assert.equal(list.children[0], firstRow);
  assert.equal(firstRow.children[1].textContent, '76.0 C');
  assert.match(page.elements['sys-health-summary'].textContent, /1 need attention/);
  await page.advance(20000);
  assert.equal(page.requests.length, 6);
  assert.ok(page.requests.slice(1).every(request => request.url.endsWith('?scope=live')));
  page.response.checks = allChecks();
  await page.advance(5000);
  assert.equal(page.requests.length, 7);
  assert.equal(page.requests[6].url, '/cgi-bin/system_health.sh');
  assert.equal(list.children[0], firstRow);
});

test('partial updates preserve warnings from slower checks and manual refresh forces all checks', async () => {
  const checks = allChecks();
  checks[7].status = 'error';
  const page = await mount(checks);
  page.response.checks = [checks[0]];
  await page.advance(5000);
  assert.match(page.elements['sys-health-summary'].textContent, /1 need attention/);
  await page.elements['sys-health-refresh'].listeners.click();
  assert.equal(page.requests.at(-1).url, '/cgi-bin/system_health.sh');
});

test('hidden pages pause polling and resume with a full refresh', async () => {
  const page = await mount();
  await page.visibility(true);
  await page.advance(60000);
  assert.equal(page.requests.length, 1);
  assert.match(page.elements['sys-health-summary'].textContent, /paused/);
  await page.visibility(false);
  assert.equal(page.requests.length, 2);
  assert.equal(page.requests[1].url, '/cgi-bin/system_health.sh');
  await page.advance(5000);
  assert.equal(page.requests.length, 3);
});

test('leaving the tab cancels an active request and releases polling and visibility hooks', async () => {
  const page = await mount();
  page.response.wait = true;
  await page.advance(5000);
  assert.equal(page.requests.length, 2);
  await page.removeTab();
  assert.equal(page.requests[1].options.signal.aborted, true);
  assert.equal(page.timers.size, 0);
  assert.equal(page.listeners.visibilitychange, undefined);
  await page.advance(60000);
  assert.equal(page.requests.length, 2);
});

test('hiding the page cancels an active request without reporting a timeout, then resumes all checks', async () => {
  const page = await mount();
  page.response.wait = true;
  await page.advance(5000);
  await page.visibility(true);
  assert.equal(page.requests[1].options.signal.aborted, true);
  assert.match(page.elements['sys-health-summary'].textContent, /paused/);
  assert.doesNotMatch(page.elements['sys-health-summary'].textContent, /timed out/);
  await page.advance(30000);
  assert.equal(page.requests.length, 2);
  page.response.wait = false;
  await page.visibility(false);
  assert.equal(page.requests.length, 3);
  assert.equal(page.requests[2].url, '/cgi-bin/system_health.sh');
  assert.match(page.elements['sys-health-summary'].textContent, /All checks OK/);
});

test('a rapid hide/show restarts the aborted request as a full check', async () => {
  const page = await mount();
  page.response.wait = true;
  await page.advance(5000);
  const hidden = page.visibility(true);
  page.response.wait = false;
  const visible = page.visibility(false);
  await Promise.all([hidden, visible]);
  assert.equal(page.requests.length, 3);
  assert.equal(page.requests[2].url, '/cgi-bin/system_health.sh');
  assert.match(page.elements['sys-health-summary'].textContent, /All checks OK/);
});

test('requests never overlap, timeouts show stale data and automatic retries recover', async () => {
  const page = await mount();
  page.response.wait = true;
  await page.advance(5000);
  await page.elements['sys-health-refresh'].listeners.click();
  await page.advance(10000);
  assert.equal(page.requests.length, 2);
  await page.advance(5000);
  assert.equal(page.requests[1].options.signal.aborted, true);
  assert.match(page.elements['sys-health-summary'].textContent, /Request timed out.*stale/);
  page.response.wait = false;
  await page.advance(5000);
  assert.match(page.elements['sys-health-summary'].textContent, /All checks OK/);
  assert.equal(page.elements['sys-health-list'].children.length, 1);
});
