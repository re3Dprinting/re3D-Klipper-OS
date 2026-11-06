const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');

const root = path.join(__dirname, '..');
const filesystem = path.join(root, 'src', 'modules', 'fullpageos', 'filesystem');
const commands = path.join(filesystem, 'home', 'pi', 'printer_data', 'config', 'src', 'system_config', 'shell_commands');
const www = path.join(filesystem, 'opt', 'mconfig', 'www');
const html = fs.readFileSync(path.join(www, 'tabs', 'system.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][1][1];
const bash = process.env.BASH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles, 'Git', 'bin', 'bash.exe') : 'bash');

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-update-branch-'));
  t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
  function run(source, {body = '', method = 'GET', length = Buffer.byteLength(body)} = {}) {
    const result = spawnSync(bash, ['-c', 'export TEST_ROOT="$PWD"\n' + source], {
      cwd: directory, input: body, encoding: 'utf8', timeout: 30000,
      env: {...process.env, REQUEST_METHOD: method, CONTENT_LENGTH: String(length), GIT_TERMINAL_PROMPT: '0'}
    });
    assert.ifError(result.error);
    return result;
  }
  function ok(source, options) {
    const result = run(source, options);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    return result.stdout.trim();
  }
  const mapPaths = source => source
    .replaceAll('/etc/re3d-airgap/enabled', './airgap-enabled')
    .replaceAll('/run/re3d-airgap/enabled', './airgap-applied')
    .replaceAll('/usr/local/bin/re3d-update-config.sh', './helper.sh')
    .replaceAll('/opt/re3d-os-src', '${TEST_ROOT}/deployed')
    .replaceAll('/home/pi/klipper', '${TEST_ROOT}/no-klipper');
  const helper = fs.readFileSync(path.join(commands, 're3d-update-config.sh'), 'utf8')
    .replace('/etc/re3d-os-update/branch', '${TEST_ROOT}/settings/branch')
    .replace('https://github.com/re3Dprinting/re3D-Klipper-OS.git', '${TEST_ROOT}/remote.git');
  fs.writeFileSync(path.join(directory, 'helper.sh'), helper);
  fs.writeFileSync(path.join(directory, 'helper.source'), helper);
  const bootstrap = mapPaths(fs.readFileSync(path.join(commands, 're3d-os-update'), 'utf8'));
  fs.writeFileSync(path.join(directory, 'bootstrap'), bootstrap);
  fs.writeFileSync(path.join(directory, 'bootstrap.source'), bootstrap);
  for (const name of ['_cgi_common.sh', 'update_branch.sh', 'check_os_update.sh']) {
    fs.writeFileSync(path.join(directory, name),
      mapPaths(fs.readFileSync(path.join(www, 'cgi-bin', name), 'utf8')));
  }
  fs.mkdirSync(path.join(directory, 'seed', 'tools'), {recursive: true});
  fs.writeFileSync(path.join(directory, 'seed', 'tools', 're3d-os-update.sh'), `#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$1" >> "\${TEST_ROOT}/runs"
cp "\${TEST_ROOT}/bootstrap.source" "\${TEST_ROOT}/bootstrap"
cp "\${TEST_ROOT}/helper.source" "\${TEST_ROOT}/helper.sh"
`);
  ok(`git init --bare remote.git
git init -b devel seed
git -C seed config user.name Test
git -C seed config user.email test@example.invalid
git -C seed add .
git -C seed commit -m initial
git -C seed tag test-tag
git -C seed checkout -b feature/test-update
echo feature > seed/feature.txt
git -C seed add .
git -C seed commit -m feature
git -C seed remote add origin "$TEST_ROOT/remote.git"
git -C seed push origin devel feature/test-update --tags
git --git-dir=remote.git symbolic-ref HEAD refs/heads/devel`);
  return {
    directory, run, ok,
    get: () => run('bash update_branch.sh'),
    save: branch => run('bash update_branch.sh', {method: 'POST', body: branch}),
    branch: () => ok('git -C deployed symbolic-ref --short HEAD'),
    saved: () => fs.readFileSync(path.join(directory, 'settings', 'branch'), 'utf8').trim()
  };
}

test('saved branch survives two updates that reinstall the bootstrap; reset and CLI overrides work', t => {
  const f = fixture(t);
  assert.match(f.get().stdout, /200 OK[\s\S]*\r?\ndevel\s*$/);
  assert.equal(f.save('feature/test-update').status, 0);
  f.ok('bash bootstrap');
  f.ok('bash bootstrap');
  assert.equal(f.branch(), 'feature/test-update');
  assert.equal(f.saved(), 'feature/test-update');
  assert.ok(fs.existsSync(path.join(f.directory, 'deployed', 'feature.txt')));
  assert.match(f.ok('bash check_os_update.sh'), /UP_TO_DATE branch=feature\/test-update/);
  f.ok('bash bootstrap devel');
  assert.equal(f.branch(), 'devel');
  assert.equal(f.saved(), 'feature/test-update');
  assert.match(f.ok('bash check_os_update.sh'), /UPDATE_AVAILABLE branch=feature\/test-update/);
  f.ok('bash bootstrap test-tag');
  assert.equal(f.saved(), 'feature/test-update');
  f.ok('bash bootstrap');
  assert.equal(f.branch(), 'feature/test-update');
  assert.equal(f.save('devel').status, 0);
  f.ok('bash bootstrap');
  assert.equal(f.branch(), 'devel');
  assert.equal(f.saved(), 'devel');
  assert.deepEqual(fs.readFileSync(path.join(f.directory, 'runs'), 'utf8').trim().split('\n'),
    ['feature/test-update', 'feature/test-update', 'devel', 'test-tag', 'feature/test-update', 'devel']);
});

test('invalid, missing, oversized, truncated and offline saves preserve the previous setting', t => {
  const f = fixture(t);
  assert.equal(f.save('feature/test-update').status, 0);
  for (const branch of ['', '-option', 'HEAD', '../bad', 'bad..name', 'with space', 'a\nb',
    '$(touch injected)', '@{-1}', 'x'.repeat(256)]) {
    const response = f.save(branch);
    assert.notEqual(response.status, 0, branch);
    assert.match(response.stdout, /400 Bad Request/, branch);
    assert.equal(f.saved(), 'feature/test-update');
  }
  assert.match(f.save('missing').stdout, /422 Unprocessable Entity/);
  assert.match(f.save('a;b').stdout, /422 Unprocessable Entity/);
  assert.match(f.run('bash update_branch.sh', {method: 'POST', body: 'devel', length: 10}).stdout,
    /400 Bad Request/);
  assert.match(f.run('bash update_branch.sh', {method: 'DELETE'}).stdout, /405 Method Not Allowed/);
  fs.renameSync(path.join(f.directory, 'remote.git'), path.join(f.directory, 'offline.git'));
  assert.match(f.save('devel').stdout, /502 Bad Gateway/);
  assert.equal(f.saved(), 'feature/test-update');
  assert.equal(fs.existsSync(path.join(f.directory, 'injected')), false);
});

test('deleted branches and failed fetches abort instead of deploying the current HEAD', t => {
  const f = fixture(t);
  assert.equal(f.save('feature/test-update').status, 0);
  f.ok('bash bootstrap');
  f.ok('git -C seed push origin --delete feature/test-update');
  assert.match(f.run('bash check_os_update.sh').stdout, /ERROR requested branch/);
  assert.notEqual(f.run('bash bootstrap').status, 0);
  fs.renameSync(path.join(f.directory, 'remote.git'), path.join(f.directory, 'offline.git'));
  assert.match(f.run('bash bootstrap').stdout, /ERROR: git fetch failed/);
  assert.equal(fs.readFileSync(path.join(f.directory, 'runs'), 'utf8'), 'feature/test-update\n');
});

test('invalid saved settings, save failures and air-gap policy report errors', t => {
  const f = fixture(t);
  fs.mkdirSync(path.join(f.directory, 'settings'));
  fs.writeFileSync(path.join(f.directory, 'settings', 'branch'), 'bad..ref\n');
  assert.match(f.get().stdout, /500 Internal Server Error[\s\S]*Invalid update branch/);
  assert.notEqual(f.run('bash bootstrap').status, 0);
  fs.unlinkSync(path.join(f.directory, 'settings', 'branch'));
  fs.rmdirSync(path.join(f.directory, 'settings'));
  fs.writeFileSync(path.join(f.directory, 'settings'), 'not a directory');
  assert.match(f.save('devel').stdout, /500 Internal Server Error/);
  for (const marker of ['airgap-enabled', 'airgap-applied']) {
    fs.writeFileSync(path.join(f.directory, marker), '');
    assert.match(f.save('devel').stdout, /403 Forbidden/);
    assert.notEqual(f.run('bash bootstrap').status, 0);
    fs.unlinkSync(path.join(f.directory, marker));
  }
});

async function mount({branch = 'devel', offline = false, loadFailure = false} = {}) {
  const elements = new Map();
  const calls = [];
  const response = {saveError: '', holdSave: false};
  function el(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false, style: {}, hidden: true,
      classList: {add() {}, remove() {}}, focus() {},
      addEventListener(event, callback) { this[event] = callback; }
    });
    return elements.get(id);
  }
  const window = {location: {href: ''}};
  vm.runInNewContext(script, {
    document: {getElementById: el, addEventListener() {}, removeEventListener() {}},
    window, Date, setTimeout, clearTimeout,
    async fetch(url, options = {}) {
      calls.push({url, options});
      if (url === '/cgi-bin/update_branch.sh') {
        if (options.method === 'POST') {
          if (response.holdSave) await new Promise(resolve => { response.release = resolve; });
          if (response.saveError) return {ok: false, text: async () => response.saveError};
          branch = options.body;
        } else if (loadFailure) return {ok: false, text: async () => 'Settings unavailable'};
        return {ok: true, text: async () => branch};
      }
      if (url.startsWith('/cgi-bin/check_os_update.sh')) {
        return {ok: true, text: async () => `UP_TO_DATE branch=${branch}`};
      }
      return {
        ok: true, text: async () => 'mainsail',
        json: async () => ({enabled: false, applied: false, can_update: !offline, mode: 'off'})
      };
    }
  });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  return {el, calls, response, window, flush,
    async edit(value) { el('update-branch').value = value; el('update-branch').input(); },
    async save() { el('update-branch-save').click(); await flush(); }
  };
}

test('DevOps is collapsed and saved branch loads, saves, auto-checks and resets to devel', async () => {
  assert.match(html, /<details class="sys-devops">\s*<summary>DevOps<\/summary>/);
  const f = await mount({branch: 'feature/test-update'});
  assert.equal(f.el('update-branch').value, 'feature/test-update');
  assert.equal(f.el('update-branch-current').textContent, 'Update branch: feature/test-update');
  assert.equal(f.el('btn-update').disabled, false);
  await f.edit('feature/new');
  assert.equal(f.el('btn-update').disabled, true);
  f.el('btn-update').click();
  assert.equal(f.el('update-modal').hidden, true);
  await f.save();
  const save = f.calls.find(call => call.options.method === 'POST');
  assert.equal(save.options.body, 'feature/new');
  assert.equal(save.options.headers['Content-Type'], 'text/plain; charset=utf-8');
  assert.match(f.el('update-branch-status').textContent, /Saved.*feature\/new/);
  assert.equal(f.el('out-update').textContent, 'UP_TO_DATE branch=feature/new');
  assert.equal(f.el('btn-update').disabled, false);
  f.el('update-branch-reset').click();
  await f.flush();
  assert.equal(f.el('update-branch').value, 'devel');
  assert.equal(f.el('update-branch-current').textContent, 'Update branch: devel');
  f.el('btn-update').click();
  assert.equal(f.el('update-modal').hidden, false);
  f.el('update-run').click();
  assert.match(f.window.location.href, /updating.html\?start=1&reflash=1/);
});

test('failed saves retain saved branch and block dirty updates; saving disables actions', async () => {
  const f = await mount();
  await f.edit('missing');
  f.response.saveError = 'Branch not found';
  await f.save();
  assert.match(f.el('update-branch-status').textContent, /Branch not saved: Branch not found/);
  assert.equal(f.el('update-branch-current').textContent, 'Update branch: devel');
  assert.equal(f.el('btn-update').disabled, true);
  f.response.saveError = '';
  f.response.holdSave = true;
  f.el('update-branch-save').click();
  await f.flush();
  for (const id of ['btn-update', 'btn-check-update', 'update-branch', 'update-branch-save', 'update-branch-reset']) {
    assert.equal(f.el(id).disabled, true, id);
  }
  f.response.release();
  await f.flush();
  assert.equal(f.el('btn-update').disabled, false);
});

test('offline or unreadable settings never enable update actions', async () => {
  const offline = await mount({offline: true});
  assert.equal(offline.el('btn-update').disabled, true);
  assert.equal(offline.el('update-branch-save').disabled, true);
  const failed = await mount({loadFailure: true});
  assert.equal(failed.el('btn-update').disabled, true);
  assert.match(failed.el('update-branch-status').textContent, /Settings unavailable/);
  await failed.edit('devel');
  await failed.save();
  assert.equal(failed.el('btn-update').disabled, false);
});

test('shell entry points parse and both deployment paths install the shared settings helper', () => {
  for (const filename of [
    path.join(commands, 're3d-update-config.sh'), path.join(commands, 're3d-os-update'),
    path.join(www, 'cgi-bin', 'update_branch.sh'), path.join(www, 'cgi-bin', 'check_os_update.sh')
  ]) {
    const result = spawnSync(bash, ['-n'], {input: fs.readFileSync(filename), encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
  }
  for (const filename of [path.join(root, 'src', 'modules', 'fullpageos', 'start_chroot_script'),
    path.join(root, 'tools', 're3d-os-update.sh')]) {
    assert.match(fs.readFileSync(filename, 'utf8'), /find .* -maxdepth 1 -type f ! -name '\*\.cfg' -print0/);
  }
});
