const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
const extension = path.join(filesystem, 'home', 'pi', 're3d-kiosk-ui');
const worker = fs.readFileSync(path.join(extension, 'thingiverse-background.js'), 'utf8');
const backgroundFile = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8')).background.service_worker;
const background = fs.readFileSync(path.join(extension, backgroundFile), 'utf8');
const scraper = fs.readFileSync(path.join(extension, 'thingiverse-collector.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const design = {title: 'Printed vase', image: 'https://cdn.thingiverse.com/assets/vase.png'};
const emptyCache = {designs: [], updatedAt: null, checkedAt: null, lastError: null};

async function collector(options = {}) {
  const calls = [];
  const messages = [];
  const hooks = {};
  const state = {
    now: Date.parse('2026-11-12T22:00:00Z'),
    cached: {...emptyCache},
    policy: {enabled: false, applied: false, local: true, security_mode: 'off'},
    results: [{designs: [design]}], ...options
  };
  const event = name => ({addListener(callback) { hooks[name] = callback; }});
  const context = vm.createContext({
    URL, AbortSignal, Date: {now: () => state.now, parse: Date.parse},
    setTimeout(callback, delay) { state.now += delay; queueMicrotask(callback); },
    console: {error() {}},
    fetch: async (url, opts) => {
      calls.push({url, opts});
      if (state.fetchErrorFor && url.endsWith(state.fetchErrorFor)) throw new TypeError('Failed to fetch');
      if (url.endsWith('/auth/airgap')) return {ok: true, json: async () => state.policy};
      if (state.cacheError || (state.postError && opts.method === 'POST')) {
        return {ok: false, status: 503, json: async () => ({error: 'Cache unavailable'})};
      }
      if (opts.method === 'POST') {
        const payload = JSON.parse(opts.body);
        state.cached.checkedAt = new Date(state.now).toISOString();
        if (payload.designs) {
          state.cached.designs = payload.designs;
          state.cached.updatedAt = state.cached.checkedAt;
          state.cached.lastError = null;
        } else state.cached.lastError = payload.error;
      }
      return {ok: true, json: async () => state.cached};
    },
    chrome: {
      runtime: {onMessage: {addListener(callback) { messages.push(callback); }}},
      alarms: {onAlarm: event('alarm'), async get() { return null; },
        async create(name, options) { calls.push({alarm: name, options}); }},
      storage: {session: {async get() { return {}; }, async remove() {}}},
      tabs: {
        async create(options) { calls.push({create: options}); return {id: 20}; },
        async sendMessage(id, message) {
          calls.push({read: id, message});
          const result = state.results.length > 1 ? state.results.shift() : state.results[0];
          if (result instanceof Error) throw result;
          return result;
        },
        async remove(id) { calls.push({remove: id}); },
        onRemoved: event('removed')
      },
      webNavigation: {onErrorOccurred: event('error'), onCreatedNavigationTarget: event('target')}
    }
  });
  context.importScripts = () => vm.runInContext(worker, context);
  vm.runInContext(background, context);
  await flush();
  async function refresh(force = false, local = true) {
    const url = local ? 'http://localhost:8081/#news' : 'http://remote-printer/#news';
    return new Promise(resolve => {
      for (const listener of messages) listener(
        {type: 're3d-refresh-designs', force}, {url, tab: {id: 1, url}}, resolve);
    });
  }
  async function publish(sender = {url: 'https://www.thingiverse.com/re3dprinting/designs', frameId: 0, tab: {id: 30}}) {
    return new Promise(resolve => {
      for (const listener of messages) listener({type: 're3d-publish-designs', designs: [design]}, sender, resolve);
    });
  }
  return {calls, state, refresh, publish, hooks};
}

test('scheduled checks collect displayed metadata in an inactive tab and save it on the printer', async () => {
  const f = await collector();
  assert.equal(f.calls[0].options.periodInMinutes, 360);
  const result = await f.refresh();
  assert.equal(result.ok, true);
  assert.equal(f.calls.find(call => call.create).create.active, false);
  assert.equal(f.state.cached.designs[0].title, design.title);
  assert.equal(f.calls.filter(call => call.remove === 20).length, 1);
  assert.ok(f.calls.some(call => call.url?.endsWith('thingiverse_designs.py') && call.opts.method === 'POST'));
});

test('automatic checks honor the exact six-hour threshold; manual refresh bypasses it', async () => {
  const f = await collector();
  f.state.cached.checkedAt = new Date(f.state.now - 6 * 60 * 60 * 1000 + 1).toISOString();
  assert.equal((await f.refresh()).skipped, true);
  assert.equal(f.calls.some(call => call.create), false);
  f.state.now++;
  assert.equal((await f.refresh()).ok, true);
  assert.equal((await f.refresh(true)).ok, true);
  assert.equal(f.calls.filter(call => call.create).length, 2);
});

test('concurrent local refreshes share one collection; remote callers cannot trigger browsing', async () => {
  const f = await collector();
  await Promise.all([f.refresh(true), f.refresh(true)]);
  assert.equal(f.calls.filter(call => call.create).length, 1);
  const remote = await collector();
  assert.match((await remote.refresh(true, false)).error, /local touchscreen/);
  assert.equal(remote.calls.some(call => call.url), false);
});

test('air-gap, applied isolation, Lockdown, and invalid policies never contact Thingiverse', async () => {
  for (const policy of [
    {enabled: true}, {applied: true}, {security_mode: 'lockdown'}, {local: false}, {enabled: undefined}
  ]) {
    const f = await collector();
    Object.assign(f.state.policy, policy);
    const result = await f.refresh(true);
    assert.ok(result.skipped || result.error);
    assert.equal(f.calls.some(call => call.create), false);
  }
});

test('verification pages and the exact 45-second timeout retain saved designs and publish failure status', async () => {
  for (const results of [[{pending: true, error: 'Thingiverse verification page'}], [{designs: []}]]) {
    const f = await collector({results, cached: {...emptyCache, designs: [design], updatedAt: '2026-11-11T22:00:00Z'}});
    const before = f.state.now;
    const result = await f.refresh(true);
    assert.ok(result.error);
    assert.equal(f.state.cached.designs[0].title, design.title);
    assert.equal(f.state.cached.updatedAt, '2026-11-11T22:00:00Z');
    assert.ok(f.state.cached.lastError);
    assert.equal(f.calls.filter(call => call.remove === 20).length, 1);
    assert.equal(f.state.now - before, 45000);
  }
});

test('initial content-script readiness is retried, but cache failures are explicit', async () => {
  const f = await collector({results: [new Error('Could not establish connection. Receiving end does not exist.'), {designs: [design]}]});
  assert.equal((await f.refresh()).ok, true);
  assert.equal(f.calls.filter(call => call.read).length, 2);
  const broken = await collector({cacheError: true});
  assert.match((await broken.refresh(true)).error, /Cache unavailable/);
  assert.equal(broken.calls.some(call => call.create), false);
  const statusFailure = await collector({postError: true, results: [{error: 'Verification page'}]});
  assert.match((await statusFailure.refresh(true)).error, /Verification page.*Could not record.*Cache unavailable/);
});

test('a normally opened public profile can populate the cache without background navigation', async () => {
  const f = await collector();
  assert.equal((await f.publish()).ok, true);
  assert.equal(f.state.cached.designs[0].title, design.title);
  assert.equal(f.calls.some(call => call.create), false);
  assert.match((await f.publish({url: 'https://evil.test/re3dprinting/designs', frameId: 0, tab: {id: 30}})).error,
    /Only the public re3D/);
  f.state.policy.enabled = true;
  assert.equal((await f.publish()).skipped, true);
});

test('network failures identify the printer endpoint without starting Thingiverse collection', async () => {
  for (const endpoint of ['/auth/airgap', '/cgi-bin/thingiverse_designs.py']) {
    const f = await collector({fetchErrorFor: endpoint});
    assert.equal((await f.refresh(true)).error,
      'Unable to GET http://localhost:8081' + endpoint + ': Failed to fetch');
    assert.equal(f.calls.some(call => call.create), false);
  }
});

function card(id, title, owner = 're3dprinting') {
  const href = 'https://www.thingiverse.com/thing:' + id;
  const links = [
    {href, textContent: '123 likes', querySelector: () => ({src: design.image})},
    {href, textContent: title, querySelector: () => null},
    {href: 'https://www.thingiverse.com/' + owner + '/designs', textContent: owner, querySelector: () => null}
  ];
  return {querySelectorAll: () => links};
}

function scrape(cards, title = 'Designs - re3dprinting - Thingiverse') {
  let listener;
  vm.runInNewContext(scraper, {
    URL, location: new URL('https://www.thingiverse.com/re3dprinting/designs'),
    console: {error() {}},
    MutationObserver: class {observe() {} disconnect() {}},
    document: {title, documentElement: {}, body: {innerText: ''}, querySelectorAll: () => cards},
    chrome: {runtime: {onMessage: {addListener(callback) { listener = callback; }}, async sendMessage() { return {ok: true}; }}}
  });
  let result;
  listener({type: 're3d-read-designs'}, {}, data => { result = data; });
  return JSON.parse(JSON.stringify(result));
}

test('DOM extraction takes the first six unique re3D design cards, not counters or other authors', () => {
  const result = scrape([
    card(99, 'Other author', 'someoneelse'), card(1, 'Newest print'), card(1, 'Duplicate'),
    ...Array.from({length: 7}, (_, index) => card(index + 2, 'Print ' + index))
  ]);
  assert.equal(result.designs.length, 6);
  assert.equal(result.designs[0].title, 'Newest print');
  assert.equal(result.designs[5].title, 'Print 4');
  assert.equal(result.designs[0].image, design.image);
  assert.deepEqual(scrape([]).designs, []);
  assert.match(scrape([], 'Just a moment...').error, /verification page/);
});
