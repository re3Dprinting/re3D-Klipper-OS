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
  assert.equal((html.match(/class="tab" data-page=/g) || []).length, 9);
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
