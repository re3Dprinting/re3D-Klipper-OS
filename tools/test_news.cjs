const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const www = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig', 'www');
const html = fs.readFileSync(path.join(www, 'tabs', 'news.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
const post = {
  title: {rendered: 'Research & stories'}, date: '2026-08-26T14:00:00',
  link: 'https://re3d.org/story/',
  _embedded: {'wp:featuredmedia': [{source_url: 'https://re3d.org/image.webp'}]}
};

function element(tag = '') {
  return {
    tag, children: [], attributes: {}, listeners: {}, textContent: '', isConnected: true,
    setAttribute(name, value) { this.attributes[name] = value; },
    appendChild(child) { this.children.push(child); },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    cloneNode() { return Object.assign(element(this.tag), {href: this.href, target: this.target, rel: this.rel}); },
    remove() { this.removed = true; }
  };
}

async function mount(options = {}) {
  const elements = Object.fromEntries(['news-page', 'news-grid', 'news-status', 'news-refresh']
    .map(id => [id, element()]));
  const storage = new Map(options.stored ? [['re3d-news-v1', options.stored]] : []);
  const calls = [];
  const state = {policy: {enabled: false, applied: false}, posts: [post], ...options};
  vm.runInNewContext(script, {
    document: {getElementById: id => elements[id], createElement: element},
    DOMParser: class { parseFromString(text) { return {body: {textContent: text}}; } },
    URL, AbortSignal,
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem(key, value) {
        if (state.storageError) throw new Error('Storage disabled');
        storage.set(key, value);
      }
    },
    fetch: async (url, opts) => {
      calls.push({url, opts});
      if (url === '/auth/airgap') {
        if (state.policyError) throw new Error('Policy unavailable');
        return {ok: true, json: async () => state.policy};
      }
      if (state.feedError) throw new Error('Network offline');
      return {ok: !state.httpError, status: 503, json: async () => state.posts};
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  return {elements, storage, calls, state};
}

function descendants(node, tag) {
  return node.children.flatMap(child => [
    ...(child.tag === tag ? [child] : []), ...descendants(child, tag)
  ]);
}

test('platform shortcuts precede stories with accessible external links and locally bundled icons', () => {
  const nav = html.match(/<nav class="news-platforms"[^>]*>([\s\S]*?)<\/nav>/);
  assert.ok(nav);
  assert.ok(html.indexOf(nav[0]) < html.indexOf('<div class="news-toolbar">'));
  const links = [...nav[1].matchAll(/<a ([^>]+)><img ([^>]+)><\/a>/g)];
  assert.equal(links.length, 8);
  const hosts = new Set();
  for (const [, anchor, image] of links) {
    hosts.add(new URL(anchor.match(/href="([^"]+)"/)[1]).hostname);
    assert.match(anchor, /target="_blank"/);
    assert.match(anchor, /rel="noopener noreferrer"/);
    assert.match(anchor, /aria-label="[^"]+ \(opens in a new tab\)"/);
    const source = image.match(/src="([^"]+)"/)[1];
    assert.ok(source.startsWith('/img/'));
    const bytes = fs.readFileSync(path.join(www, source));
    assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
    assert.equal(bytes.toString('ascii', 8, 12), 'WEBP');
  }
  assert.deepEqual(hosts, new Set([
    'www.facebook.com', 'www.instagram.com', 'www.youtube.com', 'www.thingiverse.com',
    'cults3d.com', 're3d.zendesk.com', 'www.linkedin.com', 'github.com'
  ]));
});

test('News is first and default, while explicit Machine links and guided setup are retained', () => {
  assert.equal(index.match(/class="tab" data-page="([^"]+)"/)[1], 'news');
  assert.match(index, /const TABS = \['news','machine'/);
  assert.match(index, /if \(!TABS\.includes\(name\)\) name = 'news'/);
  assert.equal((index.match(/location\.hash\s*\|\|\s*'#news'/g) || []).length, 2);
  assert.match(index, /if \(new URLSearchParams\(location\.search\)\.get\('setup'\) !== '1'\)/);
  assert.match(fs.readFileSync(path.join(www, 'setup.js'), 'utf8'), /await loadTab\(step\)/);
});

test('live stories render safe links and images and are cached for offline use', async () => {
  const page = await mount();
  const grid = page.elements['news-grid'];
  assert.equal(grid.children.length, 1);
  assert.equal(descendants(grid, 'h3')[0].children[0].textContent, post.title.rendered);
  for (const link of descendants(grid, 'a')) {
    assert.equal(link.href, post.link);
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');
  }
  const image = descendants(grid, 'img')[0];
  assert.equal(image.src, post._embedded['wp:featuredmedia'][0].source_url);
  image.listeners.error();
  assert.equal(image.removed, true);
  assert.equal(JSON.parse(page.storage.get('re3d-news-v1')).articles[0].date, '2026-08-26');
  assert.equal(page.calls[1].opts.credentials, 'omit');
  assert.equal(grid.attributes['aria-busy'], 'false');
  assert.equal(page.elements['news-refresh'].disabled, false);
});

test('offline reload retains stories without image requests and Refresh recovers', async () => {
  const online = await mount();
  const offline = await mount({stored: online.storage.get('re3d-news-v1'), feedError: true});
  assert.equal(offline.elements['news-grid'].children.length, 1);
  assert.equal(descendants(offline.elements['news-grid'], 'img').length, 0);
  assert.match(offline.elements['news-status'].textContent, /Network offline.*Showing saved stories/);
  offline.state.feedError = false;
  await offline.elements['news-refresh'].listeners.click();
  assert.match(offline.elements['news-status'].textContent, /Latest from re:3D/);
});

test('enabled or applied air-gap policy prevents all feed and image requests, including cached images', async () => {
  const online = await mount();
  for (const policy of [{enabled: true, applied: false}, {enabled: false, applied: true}]) {
    const page = await mount({stored: online.storage.get('re3d-news-v1'), policy});
    assert.equal(page.calls.length, 1);
    assert.equal(page.elements['news-grid'].children.length, 1);
    assert.equal(descendants(page.elements['news-grid'], 'img').length, 0);
    assert.match(page.elements['news-status'].textContent, /disabled by air-gap policy/);
  }
});

test('unverified isolation fails closed with a visible error', async () => {
  for (const options of [{policyError: true}, {policy: {}}]) {
    const page = await mount(options);
    assert.equal(page.calls.length, 1);
    assert.match(page.elements['news-status'].textContent, /Unable to update news/);
    assert.equal(page.elements['news-grid'].children.length, 0);
  }
});

test('empty, invalid, and failed feeds show errors without overwriting cached articles', async () => {
  const online = await mount();
  const stored = online.storage.get('re3d-news-v1');
  for (const options of [
    {posts: []}, {posts: {}}, {httpError: true},
    {posts: [{...post, link: 'javascript:alert(1)'}]},
    {posts: [{...post, date: 'bad-date'}]}
  ]) {
    const page = await mount({stored, ...options});
    assert.equal(page.storage.get('re3d-news-v1'), stored);
    assert.match(page.elements['news-status'].textContent, /Unable to update news.*Showing saved stories/);
  }
});

test('storage failures are visible without blocking live stories', async () => {
  const page = await mount({stored: '{invalid', storageError: true});
  assert.equal(page.elements['news-grid'].children.length, 1);
  assert.match(page.elements['news-status'].textContent, /could not be saved for offline use/);
  const offline = await mount({stored: '{invalid', feedError: true});
  assert.match(offline.elements['news-status'].textContent, /Saved news could not be read/);
});

test('late news responses cannot mount images after navigating away', async () => {
  const page = await mount();
  page.elements['news-page'].isConnected = false;
  const before = page.calls.length;
  await page.elements['news-refresh'].listeners.click();
  assert.equal(page.calls.length, before + 1);
  assert.equal(descendants(page.elements['news-grid'], 'img').length, 0);
});
