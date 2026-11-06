const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const vm = require('node:vm');
const {test} = require('node:test');

const root = path.join(__dirname, '..');
const filesystem = path.join(root, 'src', 'modules', 'fullpageos', 'filesystem');
const prepare = path.join(filesystem, 'opt', 'mconfig', 'prepare_keyboard.py');
const source = `
function virtualKeyboardChromeExtension_open() {
  if (top != self) throw new Error('Cannot relay to an inaccessible parent');
  document.opened = true;
}
function virtualKeyboardChromeExtension_click(value) {
  if (top != self) throw new Error('Cannot relay keys to an inaccessible parent');
  document.value += value;
}
if (top == self) {
  document.ready = true;
  document.focus = virtualKeyboardChromeExtension_open;
  document.key = virtualKeyboardChromeExtension_click;
} else {
  document.relay = true;
}
`;

function fixture(callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-keyboard-'));
  try {
    const target = path.join(directory, 'script.js');
    fs.writeFileSync(target, source);
    const run = () => spawnSync(process.env.PYTHON || 'python', ['-B', '-c',
      'import runpy,sys; from pathlib import Path; runpy.run_path(sys.argv[1])["prepare"](Path(sys.argv[2]))',
      prepare, target], {encoding:'utf8'});
    callback({target, run, directory});
  } finally {
    fs.rmSync(directory, {recursive:true, force:true});
  }
}

test('local cross-origin frames create their own keyboard for PINs and ordinary text', () => {
  fixture(({target, run}) => {
    const result = run();
    assert.equal(result.status, 0, result.stderr);
    const patched = fs.readFileSync(target, 'utf8');
    for (const hostname of ['localhost', '127.0.0.1', '[::1]']) {
      for (const embedded of [false, true]) {
        const self = {};
        const document = {value:''};
        const context = {location:{hostname}, self, top:embedded ? {} : self, document};
        vm.runInNewContext(patched, context);
        assert.equal(document.ready, true);
        assert.equal(document.relay, undefined);
        document.focus();
        assert.equal(document.opened, true);
        for (const value of ['2', '4', '6', '8', 'G', '2', '8', ' ', 'X']) document.key(value);
        assert.equal(document.value, '2468G28 X');
      }
    }
  });
});

test('non-local pages preserve upstream top-level and iframe behavior', () => {
  fixture(({target, run}) => {
    assert.equal(run().status, 0);
    const patched = fs.readFileSync(target, 'utf8');
    for (const embedded of [false, true]) {
      const self = {};
      const document = {};
      vm.runInNewContext(patched, {
        location:{hostname:'printer.example'}, self, top:embedded ? {} : self, document
      });
      assert.equal(document.ready, embedded ? undefined : true);
      assert.equal(document.relay, embedded ? true : undefined);
    }
  });
});

test('integration is idempotent and preserves the extension source and file mode', () => {
  fixture(({target, run}) => {
    const mode = fs.statSync(target).mode & 0o777;
    assert.equal(run().status, 0);
    const once = fs.readFileSync(target, 'utf8');
    assert.ok(once.includes(source));
    assert.equal(run().status, 0);
    assert.equal(fs.readFileSync(target, 'utf8'), once);
    assert.equal(fs.statSync(target).mode & 0o777, mode);
  });
});

test('unsupported or damaged keyboard integrations fail without overwriting the extension', () => {
  fixture(({target, run, directory}) => {
    for (const broken of ['Unexpected extension update', '// re3D: render the virtual keyboard in each local document.\n(function(top) {\n']) {
      fs.writeFileSync(target, broken);
      const result = run();
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /Unsupported virtual keyboard|Incomplete re3D virtual keyboard/);
      assert.equal(fs.readFileSync(target, 'utf8'), broken);
      assert.deepEqual(fs.readdirSync(directory), ['script.js']);
    }
  });
});

test('fresh images and live updates prepare the keyboard without weakening browser or input policy', () => {
  const build = fs.readFileSync(path.join(root, 'src', 'modules', 'fullpageos', 'start_chroot_script'), 'utf8');
  assert.ok(build.indexOf('python3 /opt/mconfig/prepare_keyboard.py') > build.indexOf('git clone --depth 1 https://github.com/re3Dprinting/virtual_keyboard.git'));
  const installer = fs.readFileSync(path.join(filesystem, 'opt', 'mconfig', 'install_auth.sh'), 'utf8');
  assert.match(installer, /if \[\[ "\$MODE" == "live" \]\]; then\s+python3 \/opt\/mconfig\/prepare_keyboard.py/);
  const update = fs.readFileSync(path.join(__dirname, 're3d-os-update.sh'), 'utf8');
  assert.match(update, /for security_asset in [^\n]*prepare_keyboard.py; do/);
  const policy = fs.readFileSync(path.join(filesystem, 'opt', 'mconfig', 'security_policy.py'), 'utf8');
  assert.match(policy, /"URLBlocklist": \["\*"\]/);
  assert.match(policy, /"URLAllowlist": \["http:\/\/\.localhost:80\/", "http:\/\/\.localhost:8081\/"\]/);
});
