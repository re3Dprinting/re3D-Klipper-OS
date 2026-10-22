const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const launcher = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
  'fullpageos', 'filesystem', 'opt', 'custompios', 'scripts', 'run_onepageos'), 'utf8');
const bash = process.env.BASH || (process.platform === 'win32'
  ? path.join(process.env.ProgramFiles, 'Git', 'bin', 'bash.exe') : 'bash');

function launch({ firstboot = false, status = '503', disabled = false } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 're3d-kiosk-'));
  try {
    if (firstboot) fs.writeFileSync(path.join(directory, 'firstboot-splash'), '');
    fs.writeFileSync(path.join(directory, 'check_for_httpd'), disabled ? 'disabled\n' : 'enabled\n');
    const script = launcher
      .replaceAll('/etc/firstboot-splash', './firstboot-splash')
      .replaceAll('/boot/firmware/check_for_httpd', './check_for_httpd')
      .replaceAll('/opt/custompios/scripts/get_url', 'get_url')
      .replaceAll('%BROWSER_START_SCRIPT%', 'start_browser');
    const result = spawnSync(bash, ['-s'], {
      cwd: directory,
      input: `
swaybg() { :; }
get_url() { echo get_url >&2; echo http://localhost; }
curl() { echo curl >&2; echo '${status}'; }
start_browser() { echo browser >&2; }
sleep() { exit 0; }
${script}`,
      encoding: 'utf8',
      timeout: 3000
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr);
    return result.stderr.trim().split(/\r?\n/).filter(Boolean);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('first boot launches the local splash without resolving or probing the web URL', () => {
  assert.deepEqual(launch({ firstboot: true }), ['browser']);
});

for (const status of ['200', '302', '401']) {
  test(`normal boot launches the browser for HTTP ${status}`, () => {
    assert.deepEqual(launch({ status }), ['get_url', 'curl', 'browser']);
  });
}

test('normal boot keeps waiting when the web server is unavailable', () => {
  assert.deepEqual(launch(), ['get_url', 'curl']);
});

test('normal boot still honors a disabled HTTP check', () => {
  assert.deepEqual(launch({ disabled: true }), ['get_url', 'curl', 'browser']);
});
