const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const tabs = path.join(__dirname,
  '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig', 'www', 'tabs');
const html = fs.readFileSync(path.join(tabs, 'network.html'), 'utf8');
const systemHtml = fs.readFileSync(path.join(tabs, 'system.html'), 'utf8');
const indexHtml = fs.readFileSync(path.join(tabs, '..', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element() {
  return {
    textContent: '', innerHTML: '', style: {}, children: [], listeners: {},
    classList: { add() {}, remove() {} },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    appendChild(child) { this.children.push(child); }
  };
}

async function mount(info = { hostname: 'printer', interfaces: [] }) {
  const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)]
    .map(match => [match[1], element()]));
  elements['host-form'].action = '/cgi-bin/set_hostname.sh';
  const requests = [];
  const response = { info, ok: true };
  vm.runInNewContext(script, {
    document: {
      getElementById: id => elements[id] || null,
      createElement: element
    },
    URLSearchParams,
    FormData: class { *[Symbol.iterator]() { yield ['hostname', 'renamed-printer']; } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url === '/cgi-bin/net_info.sh') {
        if (response.error) throw response.error;
        return { ok: response.ok, status: 503, json: async () => response.info };
      }
      if (url === '/cgi-bin/scan_wifi.sh') return { json: async () => [] };
      if (url === '/cgi-bin/set_hostname.sh') return { text: async () => 'Hostname updated.' };
      throw new Error(`Unexpected request: ${url}`);
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  return { elements, requests, response };
}

test('Network Info exists only in Network; System actions remain and scripts parse', () => {
  for (const id of ['netinfo', 'ni-host', 'ni-ifs']) {
    assert.equal([...html.matchAll(new RegExp(`id="${id}"`, 'g'))].length, 1);
    assert.doesNotMatch(systemHtml, new RegExp(`id="${id}"`));
  }
  assert.doesNotMatch(systemHtml, /net_info\.sh|host_ip\.sh|renderInterfaces/);
  for (const id of ['update-card', 'btn-use-mainsail', 'btn-use-klipperscreen', 'save-history-form']) {
    assert.ok(systemHtml.includes(`id="${id}"`), `System must retain ${id}`);
  }
  for (const source of [html, systemHtml]) {
    for (const match of source.matchAll(/<script>([\s\S]*?)<\/script>/g)) {
      assert.doesNotThrow(() => new vm.Script(match[1]));
    }
  }
});

test('Network Info is hidden only in guided setup, without hiding Wi-Fi or hostname controls', () => {
  assert.match(html, /<div class="card" id="network-info-card" style="margin-top:12px">/);
  assert.match(indexHtml, /body\.guided-setup #network-info-card\s*\{\s*display:\s*none\s*\}/);
  assert.doesNotMatch(indexHtml, /body\.guided-setup\s+#(?:wifi-list|wifi-status|host-form)\b/);
});

test('one request populates hostname, all interfaces, addresses, MACs and Wi-Fi banner', async () => {
  const page = await mount({
    hostname: 'printer-01',
    interfaces: [
      { if: 'eth0', mac: '00:11:22:33:44:55', ipv4: ['10.0.0.2', '10.0.0.3'], state: 'up' },
      { if: 'wlan0', mac: '66:77:88:99:aa:bb', ipv4: ['192.168.0.2'], state: 'up' },
      { if: 'eth1', mac: '', ipv4: [], state: 'down' }
    ]
  });

  assert.equal(page.elements['ni-host'].textContent, 'printer-01');
  const interfaces = page.elements['ni-ifs'].children;
  assert.equal(interfaces.length, 3);
  for (const text of ['eth0', '10.0.0.2', '10.0.0.3', '00:11:22:33:44:55', '(up)']) {
    assert.ok(interfaces[0].innerHTML.includes(text));
  }
  assert.match(interfaces[2].innerHTML, /\(down\)/);
  assert.match(interfaces[2].innerHTML, /&mdash;/);
  assert.equal(page.elements['wifi-status'].className, 'wifi-status online');
  assert.match(page.elements['wifi-status'].innerHTML, /192\.168\.0\.2/);
  assert.equal(page.requests.filter(r => r.url.endsWith('net_info.sh')).length, 1);
});

test('empty interface list retains hostname and offline Wi-Fi status', async () => {
  const page = await mount();
  assert.equal(page.elements['ni-host'].textContent, 'printer');
  assert.equal(page.elements['ni-ifs'].textContent, 'No active interfaces.');
  assert.equal(page.elements['wifi-status'].className, 'wifi-status offline');
});

test('rescanning and hostname submission refresh network info', async () => {
  const page = await mount();
  page.response.info.hostname = 'rescanned-printer';
  await page.elements['btn-scan'].listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.elements['ni-host'].textContent, 'rescanned-printer');
  page.response.info.hostname = 'renamed-printer';
  await page.elements['host-form'].listeners.submit({ preventDefault() {} });
  assert.equal(page.elements['ni-host'].textContent, 'renamed-printer');
  assert.equal(page.elements['out-host'].textContent, 'Hostname updated.');
  assert.equal(page.requests.filter(r => r.url.endsWith('net_info.sh')).length, 3);
});

test('HTTP, transport and backend errors are shown in the panel', async () => {
  for (const failure of [{ ok: false }, { error: new Error('Offline') }, { info: { error: 'no ip/ifconfig available in PATH' } }]) {
    const page = await mount();
    Object.assign(page.response, failure);
    await page.elements['btn-scan'].listeners.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(page.elements['ni-host'].textContent, 'unknown');
    assert.match(page.elements['ni-ifs'].textContent, /Unable to load network info:/);
    assert.equal(page.elements['wifi-status'].style.display, 'none');
  }
});

test('interface values render as text rather than markup', async () => {
  const page = await mount({
    hostname: 'printer',
    interfaces: [{ if: '<eth0>', mac: '<mac>', ipv4: ['<address>'], state: '<state>' }]
  });
  const markup = page.elements['ni-ifs'].children[0].innerHTML;
  assert.doesNotMatch(markup, /<eth0>|<mac>|<address>|<state>/);
  assert.match(markup, /&lt;eth0&gt;/);
});
