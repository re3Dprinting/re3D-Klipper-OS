const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const assets = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig');
const html = fs.readFileSync(path.join(assets, 'auth.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));

async function fixture(mode, pathname = '/auth/settings', protocol = 'https:', search = '', initial = {}) {
  const elements = new Map();
  const calls = [];
  const redirects = [];
  let failure = false;
  let readinessFailure = false;
  let readiness = {ready:true};
  const state = {enabled:mode !== 'off', mode, applied_mode:mode, has_pin:mode === 'lockdown', ...initial};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: false, required: false, textContent: '', checked:false,
      dataset:{}, classList:{toggle() {}}, focus() {}, reportValidity() { return true; },
      showModal() { this.open = true; }, close() { this.open = false; },
      addEventListener(event, callback) { this[event] = callback; },
      reset() {
        const names = id === 'ssh-form' ? ['ssh-current', 'ssh-new', 'ssh-confirm', 'ssh-admin']
          : ['current', 'current-pin', 'new', 'confirm', 'pin', 'pin-confirm'];
        for (const name of names) element(name).value = '';
      }
    });
    return elements.get(id);
  }
  vm.runInNewContext(source, {
    document: {getElementById: element},
    URLSearchParams,
    location: {pathname, protocol, search, hostname:'printer.local', replace(url) { redirects.push(url); }},
    confirm:() => true,
    async fetch(url, options) {
      const body = options.body ? JSON.parse(options.body) : undefined;
      calls.push([url, body]);
      const failed = failure || (readinessFailure && url === '/auth/readiness');
      if (!failure && url === '/auth/password') {
        state.mode = body.mode;
        state.enabled = body.mode !== 'off';
        state.reboot_required = (body.mode === 'lockdown') !== (state.applied_mode === 'lockdown');
      }
      return {
        ok: !failed,
        json: async () => failed ? {error:'Current pi password is incorrect'}
          : url === '/auth/status' ? {...state}
            : url === '/auth/readiness' ? readiness : {ok:true}
      };
    }
  });
  await flush();
  return {element, calls, redirects, state, fail() { failure = true; },
    failReadiness() { readinessFailure = true; },
    setReadiness(value) { readiness = value; },
    chooseMode(value) { element('mode').value = value; element('mode').change(); },
    async check() { element('check-readiness').click(); await flush(); },
    async save() { element('form').submit({preventDefault() {}}); await flush(); },
    async submit() {
    element('ssh-form').submit({preventDefault() {}});
    await flush();
  }};
}

test('Basic HTTP warns about cleartext and upgrades before configuring Lockdown', async () => {
  const f = await fixture('basic', '/auth/settings', 'http:');
  assert.match(f.element('warning').textContent, /not encrypted/);
  f.chooseMode('lockdown');
  assert.deepEqual(f.redirects, ['/auth/settings?mode=lockdown']);
  await f.save();
  assert.equal(f.calls.length, 1);
  const secure = await fixture('basic', '/auth/settings', 'https:', '?mode=lockdown');
  assert.equal(secure.element('mode').value, 'lockdown');
  assert.equal(secure.element('ssh-section').open, true);
  assert.equal(secure.element('submit').disabled, true);
  assert.deepEqual(secure.redirects, []);
  const login = await fixture('basic', '/auth/login', 'http:');
  assert.match(login.element('description').textContent, /HTTP is not encrypted/);
});

test('disabling Basic over HTTP returns settings to encrypted initial setup', async () => {
  const f = await fixture('basic', '/auth/settings', 'http:');
  f.chooseMode('off');
  await f.save();
  assert.deepEqual(f.redirects, ['/auth/settings']);
  assert.equal(f.state.mode, 'off');
});

test('PIN-only installations must confirm the shared PIN when adding administrator protection', async () => {
  const f = await fixture('off', '/auth/settings', 'https:', '', {has_pin:true});
  f.chooseMode('basic');
  assert.equal(f.element('current-row').hidden, true);
  assert.equal(f.element('current-pin-row').hidden, false);
  assert.equal(f.element('current-pin').disabled, false);
  assert.equal(f.element('current-pin').required, true);
  f.element('current-pin').value = '1234';
  f.element('new').value = f.element('confirm').value = 'new web password';
  await f.save();
  assert.deepEqual(f.calls.find(([url]) => url === '/auth/password')[1], {
    current:'', new:'new web password', mode:'basic', pin:'', current_pin:'1234'
  });
  assert.equal(f.element('current-pin').value, '');
  assert.equal(f.element('current-pin-row').hidden, true);
});

test('pi password form is available in all modes with separate administrator confirmation', async () => {
  for (const mode of ['off', 'basic', 'lockdown']) {
    const f = await fixture(mode);
    assert.equal(f.element('ssh-section').hidden, false);
    assert.equal(f.element('ssh-admin').required, mode !== 'off');
    f.element('ssh-admin').value = 'web password';
    f.element('ssh-current').value = 'raspberry';
    f.element('ssh-new').value = f.element('ssh-confirm').value = 'new pi password';
    await f.submit();
    assert.deepEqual(f.calls.find(([url]) => url === '/auth/ssh-password'), ['/auth/ssh-password', {
      current: 'web password', ssh_current: 'raspberry', ssh_new: 'new pi password'
    }]);
    assert.equal(f.element('ssh-current').value, '');
    assert.equal(f.element('ssh-new').value, '');
    assert.match(f.element('ssh-status').textContent, /pi password changed/);
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
  assert.match(f.element('ssh-status').textContent, /do not match/);
  f.element('ssh-confirm').value = 'new pi password';
  f.fail();
  await f.submit();
  assert.match(f.element('ssh-status').textContent, /incorrect/);
});

test('Lockdown explains and gates both machine prerequisites and a real SSH login', async () => {
  const f = await fixture('off');
  f.chooseMode('lockdown');
  assert.equal(f.element('submit').disabled, true);
  assert.match(f.element('save-help').textContent, /printer checks/);
  assert.equal(f.element('ssh-section').open, true);
  assert.notEqual(f.element('ssh-details').open, true);
  f.setReadiness({ready:false, error:'Set a non-default pi password first'});
  await f.check();
  assert.equal(f.element('readiness').dataset.state, 'blocked');
  assert.equal(f.element('ssh-details').open, true);
  assert.match(f.element('readiness').textContent, /non-default/);
  assert.equal(f.element('submit').disabled, true);
  f.setReadiness({ready:true});
  await f.check();
  assert.match(f.element('save-help').textContent, /test SSH login/);
  assert.equal(f.element('submit').disabled, true);
  f.element('ssh-tested').checked = true;
  f.element('ssh-tested').input();
  assert.equal(f.element('submit').disabled, false);
  await f.save();
  assert.equal(f.calls.at(-3)[0], '/auth/readiness');
  assert.equal(f.calls.at(-2)[0], '/auth/password');
  assert.match(f.element('status').textContent, /finish any print, then reboot/);
  assert.equal(f.element('pending').hidden, false);
  assert.equal(f.element('reboot-dialog').open, true);
});

test('stale readiness and failed checks cannot be mistaken for permission to enable Lockdown', async () => {
  const f = await fixture('basic');
  f.chooseMode('lockdown');
  await f.check();
  f.element('ssh-tested').checked = true;
  f.element('ssh-tested').input();
  f.setReadiness({ready:false, error:'SSH is no longer enabled'});
  await f.save();
  assert.equal(f.calls.some(([url]) => url === '/auth/password'), false);
  assert.match(f.element('status').textContent, /SSH is no longer enabled/);
  assert.equal(f.element('submit').disabled, true);
  f.fail();
  await f.check();
  assert.match(f.element('readiness').textContent, /Could not check/);
  assert.equal(f.element('readiness').dataset.state, 'blocked');
});

test('changing the pi password reruns checks with administrator confirmation but requires a fresh SSH login test', async () => {
  const f = await fixture('basic');
  f.chooseMode('lockdown');
  await f.check();
  f.element('ssh-tested').checked = true;
  f.element('ssh-admin').value = 'web password';
  f.element('ssh-new').value = f.element('ssh-confirm').value = 'new pi password';
  await f.submit();
  assert.deepEqual(f.calls.at(-1), ['/auth/readiness', {current:'web password'}]);
  assert.equal(f.element('ssh-admin').value, '');
  assert.equal(f.element('ssh-tested').checked, false);
  assert.equal(f.element('submit').disabled, true);
  assert.equal(f.element('readiness').dataset.state, 'ready');
  assert.match(f.element('save-help').textContent, /test SSH login/);
  assert.match(f.element('ssh-status').textContent, /Printer checks passed/);
  f.element('ssh-tested').checked = true;
  f.element('ssh-tested').input();
  assert.equal(f.element('submit').disabled, false);
});

test('a remaining prerequisite explains why saving is blocked after a successful password change', async () => {
  const f = await fixture('off');
  f.chooseMode('lockdown');
  f.setReadiness({ready:false, error:'Enable ssh.service before Lockdown'});
  f.element('ssh-new').value = f.element('ssh-confirm').value = 'new pi password';
  await f.submit();
  assert.equal(f.element('submit').disabled, true);
  assert.match(f.element('ssh-status').textContent, /pi password changed.*Enable ssh.service/);
  assert.match(f.element('save-help').textContent, /Enable ssh.service/);
});

test('a failed follow-up check does not imply the password change failed or require changing it again', async () => {
  const f = await fixture('off');
  f.chooseMode('lockdown');
  f.failReadiness();
  f.element('ssh-new').value = f.element('ssh-confirm').value = 'new pi password';
  await f.submit();
  assert.equal(f.element('submit').disabled, true);
  assert.equal(f.element('readiness').dataset.state, 'blocked');
  assert.match(f.element('ssh-status').textContent, /pi password changed, but the follow-up check failed/);
  assert.match(f.element('ssh-status').textContent, /do not need to change the password again/);
});

test('Basic, Off and existing Lockdown credential updates do not require new recovery setup', async () => {
  for (const mode of ['off', 'basic', 'lockdown']) {
    const f = await fixture(mode);
    assert.equal(f.element('submit').disabled, false);
    assert.equal(f.element('ssh-test-row').hidden, true);
    await f.save();
    assert.equal(f.calls.some(([url]) => url === '/auth/readiness'), false);
    assert.match(f.element('status').textContent, /protection saved/);
  }
});

test('login remains a simple password form, without setup controls', async () => {
  const f = await fixture('basic', '/auth/login');
  for (const id of ['overview', 'mode-row', 'ssh-section', 'review-section']) {
    assert.equal(f.element(id).hidden, true);
  }
  assert.equal(f.element('current').required, true);
  f.element('current').value = 'web password';
  await f.save();
  assert.deepEqual(f.calls.at(-1), ['/auth/login', {password:'web password'}]);
  assert.deepEqual(f.redirects, ['/']);
  const off = await fixture('off', '/auth/login');
  assert.deepEqual(off.redirects, ['/']);
});

test('reboot prompt is shown only when needed and reboot later never sends a reboot request', async () => {
  const basic = await fixture('basic');
  await basic.save();
  assert.notEqual(basic.element('reboot-dialog').open, true);
  const f = await fixture('lockdown');
  f.chooseMode('basic');
  await f.save();
  assert.equal(f.element('reboot-dialog').open, true);
  f.element('reboot-later').click();
  assert.equal(f.element('reboot-dialog').open, false);
  assert.equal(f.element('reboot-open').hidden, false);
  assert.equal(f.calls.some(([url]) => url === '/auth/reboot'), false);
  f.element('reboot-open').click();
  assert.equal(f.element('reboot-dialog').open, true);
  f.element('reboot-confirm').click();
  await flush();
  assert.deepEqual(f.calls.at(-1), ['/auth/reboot', {}]);
  assert.match(f.element('reboot-status').textContent, /Reboot requested/);
  assert.equal(f.element('reboot-confirm').disabled, true);
  assert.equal(f.element('reboot-reload').hidden, false);
});

test('failed reboot requests remain visible and do not claim the printer is restarting', async () => {
  const f = await fixture('lockdown');
  f.chooseMode('off');
  await f.save();
  f.fail();
  f.element('reboot-confirm').click();
  await flush();
  assert.equal(f.element('reboot-status').dataset.state, 'blocked');
  assert.equal(f.element('reboot-confirm').disabled, false);
  assert.equal(f.element('reboot-reload').hidden, true);
});

test('compact setup keeps optional help collapsed but exposes required recovery and save blockers', async () => {
  const f = await fixture('basic');
  assert.equal(f.element('ssh-section').open, false);
  assert.equal(f.element('next-link').hidden, true);
  f.chooseMode('lockdown');
  assert.equal(f.element('ssh-section').open, true);
  assert.notEqual(f.element('ssh-details').open, true);
  assert.notEqual(f.element('ssh-test-help').open, true);
  assert.equal(f.element('save-help').hidden, false);
  assert.match(f.element('save-help').textContent, /printer checks/);
  f.element('ssh-section').open = false;
  f.element('next-link').click();
  assert.equal(f.element('ssh-section').open, true);
  f.chooseMode('basic');
  assert.equal(f.element('ssh-section').open, false);
  assert.match(html, /<\/details>\s*<p id="save-help"/);
});
