const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const extension = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem',
  'home', 'pi', 're3d-kiosk-ui');
const background = fs.readFileSync(path.join(extension, 'external-background.js'), 'utf8');
const content = fs.readFileSync(path.join(extension, 'external-navigation.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function browser(returnUrl = 'http://localhost:8081/#news') {
  const tabs = new Map([[1, {id: 1, windowId: 7, url: returnUrl}]]);
  const stored = {};
  const calls = [];
  const hooks = {};
  let nextId = 2;
  let failUpdate = false;
  let failStorage = false;
  const chrome = {
    runtime: {
      getURL: name => 'chrome-extension://kiosk/' + name,
      onMessage: {addListener(callback) { hooks.message = callback; }}
    },
    storage: {session: {
      async get(key) { return {[key]: stored[key]}; },
      async set(values) {
        if (failStorage) throw new Error('Storage unavailable');
        Object.assign(stored, values);
      },
      async remove(key) { delete stored[key]; }
    }},
    tabs: {
      async query() { return [...tabs.values()]; },
      async create(options) {
        const tab = {id: nextId++, ...options};
        tabs.set(tab.id, tab);
        calls.push(['create', {...tab}]);
        return {...tab};
      },
      async update(id, options) {
        if (failUpdate) throw new Error('Navigation unavailable');
        assert.ok(tabs.has(id), 'Updated tab must exist');
        calls.push(['update', id, options]);
        Object.assign(tabs.get(id), options);
        return {...tabs.get(id)};
      },
      async remove(id) {
        calls.push(['remove', id]);
        tabs.delete(id);
        hooks.removed(id);
      },
      onRemoved: {addListener(callback) { hooks.removed = callback; }}
    },
    windows: {async update(id, options) { calls.push(['window', id, options]); }},
    webNavigation: {
      onErrorOccurred: {addListener(callback) { hooks.error = callback; }},
      onCreatedNavigationTarget: {addListener(callback) { hooks.target = callback; }}
    }
  };
  function restart() {
    vm.runInNewContext(background, {chrome, URL, importScripts() {}, console: {error() {}}});
  }
  restart();
  async function send(type, id = 1, data = {}, frameUrl) {
    return new Promise(resolve => hooks.message({type, ...data},
      {tab: {...tabs.get(id)}, url: frameUrl || tabs.get(id)?.url}, resolve));
  }
  const open = (url = 'https://www.thingiverse.com/re3dprinting/designs', frameUrl) =>
    send('re3d-open-external', 1, {url}, frameUrl);
  return {
    tabs, calls, stored, hooks, restart, send, open,
    failUpdate() { failUpdate = true; }, failStorage() { failStorage = true; }
  };
}

test('opening an external page preserves the original tab and registers its route before navigation', async () => {
  const f = browser();
  assert.equal((await f.open()).ok, true);
  assert.equal(f.tabs.get(1).url, 'http://localhost:8081/#news');
  assert.equal(f.tabs.get(2).url, 'https://www.thingiverse.com/re3dprinting/designs');
  assert.equal(f.calls[0][1].url, 'about:blank');
  assert.equal((await f.send('re3d-external-context', 2)).managed, true);
  assert.equal((await f.send('re3d-external-context', 1)).managed, false);
  f.restart();
  assert.equal((await f.send('re3d-return-to-interface', 2)).ok, true);
  assert.equal(f.tabs.get(1).active, true);
  assert.equal(f.tabs.has(2), false);
  assert.equal(f.stored['re3d-external-2'], undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.slice(-2))), [['window', 7, {focused: true}], ['remove', 2]]);
});

test('embedded Configurator links return to the existing PIN wrapper without changing its state', async () => {
  const f = browser('http://localhost/auth/kiosk?view=configurator');
  f.tabs.get(1).locked = true;
  await f.open('https://re3d.org/story/', 'http://localhost:8081/#news');
  await f.send('re3d-return-to-interface', 2);
  assert.equal(f.tabs.get(1).url, 'http://localhost/auth/kiosk?view=configurator');
  assert.equal(f.tabs.get(1).locked, true);
  assert.equal(f.calls.filter(call => call[0] === 'create').length, 1);
});

test('if the original tab disappears, returning opens its saved local URL before closing the external tab', async () => {
  const f = browser('https://localhost:8443/#news');
  await f.open();
  f.tabs.delete(1);
  await f.send('re3d-return-to-interface', 2);
  assert.equal(f.tabs.get(3).url, 'https://localhost:8443/#news');
  assert.equal(f.tabs.get(3).active, true);
  assert.equal(f.tabs.has(2), false);
});

test('untrusted callers and non-HTTPS destinations cannot start managed browsing', async () => {
  for (const url of ['http://example.org/', 'javascript:alert(1)', 'https://localhost/', 'https://user:pass@example.org/']) {
    const f = browser();
    assert.ok((await f.open(url)).error);
    assert.equal(f.tabs.size, 1);
  }
  const f = browser('https://example.org/');
  assert.match((await f.open()).error, /local printer interface/);
  assert.equal(f.tabs.size, 1);
  assert.match((await f.send('re3d-return-to-interface')).error, /return route/);
});

test('failed registration cleans up the blank tab, and failed return leaves the page available for retry', async () => {
  const f = browser();
  f.failStorage();
  assert.match((await f.open()).error, /Storage unavailable/);
  assert.equal(f.tabs.size, 1);
  const retry = browser();
  await retry.open();
  retry.failUpdate();
  assert.match((await retry.send('re3d-return-to-interface', 2)).error, /Navigation unavailable/);
  assert.equal(retry.tabs.has(2), true);
});

test('child tabs inherit the return route; main-frame failures get a return screen without touching the interface', async () => {
  const f = browser();
  await f.open();
  f.tabs.set(3, {id: 3, windowId: 7, url: 'https://example.org/next'});
  f.hooks.target({sourceTabId: 2, tabId: 3});
  await flush();
  assert.equal((await f.send('re3d-external-context', 3)).managed, true);
  for (const details of [
    {tabId: 2, frameId: 1, error: 'net::ERR_FAILED'},
    {tabId: 2, frameId: 0, error: 'net::ERR_ABORTED'},
    {tabId: 1, frameId: 0, error: 'net::ERR_FAILED'}
  ]) f.hooks.error(details);
  await flush();
  assert.equal(f.tabs.get(2).url, 'https://www.thingiverse.com/re3dprinting/designs');
  f.hooks.error({tabId: 3, frameId: 0, error: 'net::ERR_NAME_NOT_RESOLVED'});
  await flush();
  assert.equal(f.tabs.get(3).url, 'chrome-extension://kiosk/external-error.html');
  await f.send('re3d-return-to-interface', 3);
  assert.equal(f.tabs.get(1).active, true);
  assert.equal(f.tabs.has(3), false);
});

function element() {
  return {
    children: [], attributes: {}, listeners: {},
    append(...children) { this.children.push(...children); },
    appendChild(child) { this.children.push(child); },
    prepend(child) { this.children.unshift(child); },
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(type, callback) { this.listeners[type] = callback; },
    attachShadow() { this.shadowRoot = element(); return this.shadowRoot; }
  };
}

async function pageFixture(url, options = {}) {
  const hooks = {};
  const root = element();
  const news = element();
  const calls = [];
  const self = {};
  const state = {managed: true, ...options};
  vm.runInNewContext(content, {
    location: new URL(url), URL, self, top: options.frame ? {} : self,
    console: {error() {}},
    document: {
      documentElement: root, createElement: element,
      getElementById(id) { return id === 'news-page' ? news : news.children.find(child => child.id === id); },
      addEventListener(type, callback) { hooks[type] = callback; }
    },
    chrome: {runtime: {async sendMessage(message) {
      calls.push(message);
      return state.error ? {error: state.error} : {managed: state.managed, ok: true};
    }}}
  });
  await flush();
  return {hooks, root, news, calls, state};
}

test('only normal taps on News external links are intercepted, including inside the PIN wrapper', async () => {
  const f = await pageFixture('http://localhost:8081/#news', {frame: true});
  const link = {href: 'https://github.com/re3Dprinting', closest() { return f.news; }};
  let prevented = false;
  const event = {
    button: 0, target: {closest() { return link; }},
    preventDefault() { prevented = true; }, stopImmediatePropagation() {}
  };
  await f.hooks.click({...event, ctrlKey: true});
  assert.equal(prevented, false);
  await f.hooks.click(event);
  assert.equal(prevented, true);
  assert.equal(f.calls[0].type, 're3d-open-external');
  assert.equal(f.calls[0].url, link.href);
  assert.equal(f.root.children.length, 0);
  link.closest = () => null;
  await f.hooks.click(event);
  assert.equal(f.calls.length, 1);
  link.closest = () => f.news;
  f.state.error = 'Browser unavailable';
  await f.hooks.click(event);
  assert.match(f.news.children[0].textContent, /Could not open this page: Browser unavailable/);
});

test('managed external pages get an isolated return button and visible retry errors; other frames stay untouched', async () => {
  const f = await pageFixture('https://www.facebook.com/re3Dprinting/');
  const [style, button, notice] = f.root.children[0].shadowRoot.children;
  assert.match(style.textContent, /position:fixed!important/);
  assert.match(button.textContent, /Back to interface/);
  f.state.error = 'Browser unavailable';
  await button.listeners.click();
  assert.equal(f.calls.at(-1).type, 're3d-return-to-interface');
  assert.equal(button.disabled, false);
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /Browser unavailable/);
  for (const options of [{managed: false}, {frame: true}]) {
    const other = await pageFixture('https://example.org/', options);
    assert.equal(other.root.children.length, 0);
  }
});

test('extension assets and error return controls are deployed on new images and live updates', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  const updater = fs.readFileSync(path.join(__dirname, 're3d-os-update.sh'), 'utf8');
  for (const name of [manifest.background.service_worker, 'external-navigation.js', 'external-error.html']) {
    assert.ok(fs.statSync(path.join(extension, name)).size > 0);
    assert.ok(updater.includes(`"\${SRC_KIOSK_UI}/${name}"`));
  }
  assert.match(fs.readFileSync(path.join(extension, 'external-error.html'), 'utf8'),
    /<script src="external-navigation.js"><\/script>/);
});
