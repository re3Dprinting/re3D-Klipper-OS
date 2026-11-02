const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const html = fs.readFileSync(path.join(assets, 'auth.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));

async function fixture(mode) {
  const elements = new Map();
  const calls = [];
  let failure = false;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: false, required: false, textContent: '',
      addEventListener(event, callback) { this[event] = callback; },
      reset() {
        for (const name of ['ssh-current', 'ssh-new', 'ssh-confirm', 'ssh-admin']) element(name).value = '';
      }
    });
    return elements.get(id);
  }
  vm.runInNewContext(source, {
    document: {getElementById: element},
    location: {pathname: '/auth/settings'},
    async fetch(url, options) {
      calls.push([url, options.body ? JSON.parse(options.body) : undefined]);
      return {
        ok: !failure,
        json: async () => url === '/auth/status'
          ? {enabled: mode !== 'off', mode, applied_mode: mode}
          : failure ? {error: 'Current pi password is incorrect'} : {ok: true}
      };
    }
  });
  await flush();
  return {element, calls, fail() { failure = true; }, async submit() {
    element('ssh-form').submit({preventDefault() {}});
    await flush();
  }};
}

test('pi password form is available in all modes with separate administrator confirmation', async () => {
  for (const mode of ['off', 'basic', 'lockdown']) {
    const f = await fixture(mode);
    assert.equal(f.element('ssh-section').hidden, false);
    assert.equal(f.element('ssh-admin').required, mode !== 'off');
    f.element('ssh-admin').value = 'web password';
    f.element('ssh-current').value = 'raspberry';
    f.element('ssh-new').value = f.element('ssh-confirm').value = 'new pi password';
    await f.submit();
    assert.deepEqual(f.calls.at(-1), ['/auth/ssh-password', {
      current: 'web password', ssh_current: 'raspberry', ssh_new: 'new pi password'
    }]);
    assert.equal(f.element('ssh-current').value, '');
    assert.equal(f.element('ssh-new').value, '');
    assert.match(f.element('status').textContent, /pi password changed/);
  }
  assert.match(html, /id="ssh-new"[^>]*minlength="8"/);
  assert.doesNotMatch(html, /key-only SSH/);
});

test('mismatched and rejected pi passwords do not display success', async () => {
  const f = await fixture('basic');
  f.element('ssh-new').value = 'new pi password';
  f.element('ssh-confirm').value = 'different password';
  await f.submit();
  assert.equal(f.calls.length, 1);
  assert.match(f.element('status').textContent, /do not match/);
  f.element('ssh-confirm').value = 'new pi password';
  f.fail();
  await f.submit();
  assert.match(f.element('status').textContent, /incorrect/);
});
