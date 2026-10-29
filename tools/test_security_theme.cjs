const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const root = path.resolve(__dirname, '..');
const filesystem = path.join(root, 'src', 'modules', 'fullpageos', 'filesystem');
const assets = path.join(filesystem, 'opt', 'mconfig');
const css = fs.readFileSync(path.join(assets, 'security.css'), 'utf8');
const configurator = fs.readFileSync(path.join(assets, 'www', 'index.html'), 'utf8');
const mainsail = fs.readFileSync(path.join(filesystem, 'home', 'pi', 'printer_data',
  'config', 'src', 'common', 'mainsail.css'), 'utf8');

function token(text, name) {
  const match = text.match(new RegExp(`--${name}:\\s*([^;]+);`));
  assert.ok(match, `${name} must be defined`);
  return match[1].trim();
}

test('security palette matches both Configurator and Mainsail brand, surfaces, and borders', () => {
  for (const name of ['brand', 'bg', 'surface-0', 'surface-1', 'surface-2', 'surface-3', 'border', 'border-2']) {
    assert.equal(token(css, name), token(configurator, name));
    assert.equal(token(css, name), token(mainsail, `re3d-${name}`));
  }
  for (const name of ['fg', 'muted', 'dim', 'brand-600', 'brand-700', 'on-action',
    'radius', 'radius-sm', 'focus-ring']) {
    assert.equal(token(css, name), token(configurator, name));
  }
  assert.match(css, /color-scheme: dark/);
  assert.match(css, /system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, Cantarell/);
});

test('login, settings, and PIN screens share authenticated-independent styling and existing branding', () => {
  for (const name of ['auth.html', 'kiosk.html']) {
    const html = fs.readFileSync(path.join(assets, name), 'utf8');
    assert.match(html, /rel="stylesheet" href="\/auth\/security\.css"/);
    assert.match(html, /src="\/auth\/logo\.png" alt="re:3D"/);
    assert.match(html, /class="security-card/);
    assert.match(html, /class="btn-primary" type="submit"/);
    assert.doesNotMatch(html, /#245c63|#7ed4df|<style>/);
  }
  const updater = fs.readFileSync(path.join(root, 'tools', 're3d-os-update.sh'), 'utf8');
  assert.match(updater, /for security_asset in [^\n]*security\.css; do/);
});

test('controls retain visible focus, touch targets, neutral notices, and short-screen keypad layout', () => {
  assert.match(css, /input:focus, select:focus[^}]*box-shadow: var\(--focus-ring\)/);
  assert.match(css, /button:focus-visible, a:focus-visible[^}]*box-shadow: var\(--focus-ring\)/);
  assert.match(css, /min-height: 44px/);
  assert.match(css, /\.btn-primary \{[^}]*background: var\(--brand\)[^}]*color: var\(--on-action\)/);
  assert.match(css, /\.notice, \.status-message:not\(:empty\) \{[^}]*background: var\(--surface-0\)/);
  assert.match(css, /@media \(min-width: 600px\) and \(max-height: 600px\)/);
  assert.match(css, /grid-template-columns: minmax\(0,\.9fr\) minmax\(0,1\.1fr\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /\[hidden\] \{ display: none !important \}/);
});

test('primary button and secondary text meet readable contrast thresholds', () => {
  function luminance(color) {
    return [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  }
  function contrast(first, second) {
    const values = [first, second].map(name => luminance(token(css, name))).sort((a, b) => b - a);
    return (values[0] + .05) / (values[1] + .05);
  }
  assert.ok(contrast('on-action', 'brand') >= 7);
  assert.ok(contrast('muted', 'surface-1') >= 4.5);
  assert.ok(contrast('dim', 'surface-1') >= 4.5);
});
