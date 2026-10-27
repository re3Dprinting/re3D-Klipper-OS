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
