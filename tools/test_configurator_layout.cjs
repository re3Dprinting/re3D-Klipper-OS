const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
  'fullpageos', 'filesystem', 'opt', 'mconfig', 'www', 'index.html'), 'utf8');
const styles = html.match(/<style>([\s\S]*?)<\/style>/)[1];

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = styles.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(match, `${selector} must have a layout rule`);
  return match[1];
}

test('app shell fills the viewport without centering or a width cap', () => {
  const shell = rule('.wrap');
  assert.match(shell, /width:\s*100%\s*;/);
  assert.match(shell, /margin:\s*0\s*;/);
  assert.doesNotMatch(shell, /max-width|auto/);
  assert.ok(html.includes('class="wrap app"'));
});

test('only the workspace is capped while desktop and mobile navigation stay intact', () => {
  assert.match(rule('.pages'), /max-width:\s*1376px\s*;/);
  assert.match(rule('.pages'), /min-width:\s*0\s*;/);
  assert.match(rule('.app'), /grid-template-columns:\s*224px minmax\(0,1fr\)/);
  assert.match(styles, /@media \(max-width: 820px\)\s*\{\s*\.app \{ display: block \}/);
  assert.equal((html.match(/class="tab" data-page=/g) || []).length, 8);
});

test('standalone flashing and update screens match the configurator palette', () => {
  const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
  const screens = [
    { file: 'home/pi/wait.html', aliases: { 'card-border': 'border', 'text-sub': 'muted' } },
    { file: 'opt/mconfig/www/tabs/updating.html', aliases: { border: 'border', muted: 'muted' } },
  ];
  const shared = {
    bg: 'bg', card: 'surface-1', 'surface-0': 'surface-0', 'surface-2': 'surface-2',
    text: 'fg', dim: 'dim', accent: 'brand', 'accent-soft': 'brand-700',
  };
  function token(source, name) {
    const match = source.match(new RegExp(`--${name}:\\s*([^;]+);`));
    assert.ok(match, `${name} must be defined`);
    return match[1].trim();
  }
  for (const { file, aliases } of screens) {
    const screen = fs.readFileSync(path.join(filesystem, file), 'utf8');
    for (const [actual, expected] of Object.entries({ ...shared, ...aliases })) {
      assert.equal(token(screen, actual), token(styles, expected), `${file}: ${actual}`);
    }
    assert.match(screen, /color-scheme:\s*dark\s*;/);
    assert.match(screen, /background:var\(--bg\)/);
    assert.match(screen, /background:var\(--card\)/);
    assert.match(screen, /color:var\(--text\)/);
    assert.match(screen, /border-top-color:var\(--accent\)/);
    assert.match(screen, /linear-gradient\(90deg,var\(--accent-soft\),var\(--accent\)\)/);
  }
});

test('flashing status banners keep theme text and distinct error and success colors', () => {
  const flashing = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
    'fullpageos', 'filesystem', 'home', 'pi', 'wait.html'), 'utf8');
  for (const [state, color] of [['warn', 'error'], ['success', 'success']]) {
    const declarations = flashing.match(new RegExp(`\\.hint-banner\\.${state}\\{([^}]+)\\}`))[1];
    assert.ok(declarations.includes('color:var(--text)'));
    assert.ok(declarations.includes(`border-left-color:var(--${color})`));
    const expected = styles.match(new RegExp(`--${color}:\\s*([^;]+);`))[1];
    assert.ok(flashing.includes(`--${color}:${expected};`));
  }
});

test('maintenance attention banners use charcoal fills, not olive-producing yellow overlays', () => {
  const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
  for (const [file, selector] of [
    ['home/pi/wait.html', '.hint-banner.attn'],
    ['opt/mconfig/www/tabs/updating.html', '.notice'],
  ]) {
    const screen = fs.readFileSync(path.join(filesystem, file), 'utf8');
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const declarations = screen.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))[1];
    assert.match(declarations, /background:var\(--surface-0\)/, file);
    assert.match(declarations, /border-left(?:-color)?:[^;]*var\(--accent\)/, file);
    assert.doesNotMatch(screen, /rgba\(231,\s*197,\s*71,/, file);
  }
});

test('cards share the borderless charcoal treatment without changing control boundaries', () => {
  assert.match(rule('.card'), /border:\s*none;/);
  assert.match(rule('.card'), /background-image:\s*var\(--card-highlight\)/);
  assert.match(rule('input, select'), /border:\s*1px solid var\(--border-2\)/);
  assert.match(styles, /--brand-600:\s*#FFE04D/);
  assert.match(styles, /--brand-700:\s*#E6BF00/);
});

test('standalone screens use offline Lato with Roboto fallback', () => {
  const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
  for (const [file, font] of [
    ['home/pi/wait.html', '../../opt/mconfig/www/fonts/Lato-Regular.ttf'],
    ['opt/mconfig/www/tabs/updating.html', '/fonts/Lato-Regular.ttf'],
    ['opt/mconfig/www/tabs/report.html', '/fonts/Lato-Regular.ttf'],
  ]) {
    const screen = fs.readFileSync(path.join(filesystem, file), 'utf8');
    assert.ok(screen.includes(`url("${font}")`), file);
    assert.match(screen, /(?:font-family:\s*|font:\s*14px\/1\.5\s+)Lato,\s*Roboto,\s*Arial,\s*sans-serif/, file);
    const resolved = font.startsWith('/') ?
      path.join(filesystem, 'opt/mconfig/www', font) :
      path.resolve(path.dirname(path.join(filesystem, file)), font);
    assert.ok(fs.statSync(resolved).size > 0, `${file}: font exists`);
    assert.doesNotMatch(screen, /fonts\.googleapis\.com|fonts\.gstatic\.com/, file);
  }
});

test('retired switcher and video splash are not shipped or redeployed', () => {
  const filesystem = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem');
  for (const file of [
    'home/pi/url-switcher/manifest.json',
    'home/pi/url-switcher/bg.js',
    'home/pi/url-switcher/config.json',
    'home/pi/url-switcher/inject.css',
    'home/pi/url-switcher/options.html',
    'home/pi/url-switcher/Lato-Regular.ttf',
    'home/pi/url-switcher/OFL.txt',
    'home/pi/re3D_logo_animation.mp4',
    'home/pi/printer_data/config/src/system_config/shell_commands/splash_video.sh',
    'root_init/etc/systemd/system/splash_video.service',
  ]) {
    assert.equal(fs.existsSync(path.join(filesystem, file)), false, file);
  }
  const updater = fs.readFileSync(path.join(__dirname, 're3d-os-update.sh'), 'utf8');
  assert.doesNotMatch(updater, /SRC_URL_SWITCHER|DST_URL_SWITCHER/);
  assert.ok(updater.includes('systemctl disable splash_video.service'));
  assert.ok(fs.statSync(path.join(filesystem, 'boot/splash.png')).size > 0);
  const splash = fs.readFileSync(path.join(filesystem,
    'root_init/etc/systemd/system/splashscreen.service'), 'utf8');
  assert.ok(splash.includes('/boot/firmware/splash.png'));
});

test('report dark palette matches the workspace while preserving light mode and technical fonts', () => {
  const report = fs.readFileSync(path.join(__dirname, '..', 'src', 'modules',
    'fullpageos', 'filesystem', 'opt', 'mconfig', 'www', 'tabs', 'report.html'), 'utf8');
  const token = (source, name) => source.match(new RegExp(`--${name}:\\s*([^;]+);`))[1];
  for (const [reportToken, workspaceToken] of Object.entries({
    brand: 'brand', 'brand-600': 'brand-600', 'brand-700': 'brand-700',
    'brand-800': 'brand-800', bg: 'bg', 'bg-card': 'surface-1', fg: 'fg', 'gray-050': 'fg',
    'gray-300': 'muted', 'gray-400': 'dim',
  })) {
    assert.equal(token(report, reportToken), token(styles, workspaceToken), reportToken);
  }
  assert.match(report, /body\.theme-light\s*\{[^}]*--card-highlight:none;/);
  assert.match(report, /\.kv-mono\s*\{\s*font-family:ui-monospace/);
});
