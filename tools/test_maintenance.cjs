const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.join(__dirname, '..', 'src', 'modules', 'fullpageos',
  'filesystem', 'opt', 'mconfig', 'www');
const shell = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const html = fs.readFileSync(path.join(root, 'tabs', 'maintenance.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function element() {
  return {
    children: [], dataset: {}, attributes: {}, listeners: {}, textContent: '', hidden: false,
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    focus() { this.focused = true; }
  };
}

function mount() {
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)]
    .map(([, id]) => [id, element()]));
  vm.runInNewContext(script, {
    document: { getElementById: id => elements[id], createElement: element }
  });
  return elements;
}

const rows = (elements, group) => elements[`maint-${group}-list`].children;
const title = row => row.children[0].children[0].textContent;
const timer = row => row.children[0].children[1];
const action = row => row.children[1];

test('maintenance is registered in navigation, deep links, titles, and descriptions', () => {
  assert.match(shell, /class="tab" data-page="maintenance"[^]*?<\/svg>Maintenance<\/button>/);
  assert.ok(shell.match(/const TABS = \[([^\]]+)\]/)[1].includes("'maintenance'"));
  assert.match(shell, /maintenance:'Maintenance'/);
  assert.match(shell, /maintenance: 'Plan routine maintenance/);
});

test('preview has no live data, persistence, wall-clock timers, or fake QR links', () => {
  assert.match(html, /Demo only/);
  assert.match(html, /not approved maintenance guidance/);
  assert.match(html, /changes reset when you leave or reload/);
  assert.match(html, /Timers use printing hours, not wall-clock time/);
  assert.doesNotMatch(script, /\bfetch\b|localStorage|sessionStorage|setInterval|setTimeout/);
  assert.doesNotMatch(html, /<a\b|<form\b|<iframe\b/);
});

test('tasks are divided by due threshold and sorted by printing hours remaining', () => {
  const elements = mount();
  const attention = rows(elements, 'attention');
  const upcoming = rows(elements, 'upcoming');
  assert.equal(attention.length, 2);
  assert.equal(upcoming.length, 2);
  assert.equal(timer(attention[0]).textContent, '25 printing h overdue');
  assert.equal(timer(attention[0]).dataset.status, 'overdue');
  assert.equal(timer(attention[1]).textContent, 'Due now');
  assert.equal(timer(attention[1]).dataset.status, 'due');
  assert.equal(timer(upcoming[0]).textContent, 'Due in 25 printing h');
  assert.equal(timer(upcoming[1]).textContent, 'Due in 750 printing h');
  for (const group of ['attention', 'upcoming']) {
    assert.equal(elements[`maint-${group}-count`].textContent, '2');
    assert.equal(elements[`maint-${group}-empty`].hidden, true);
    assert.equal(elements[`maint-${group}-list`].hidden, false);
  }
});

test('completing a task resets its timer, moves groups, and supports undo with focus', () => {
  const elements = mount();
  action(rows(elements, 'attention')[0]).listeners.click();
  assert.equal(rows(elements, 'attention').length, 1);
  assert.equal(elements['maint-upcoming-count'].textContent, '3');
  const completed = rows(elements, 'upcoming').find(row => title(row) === 'Inspect belts and pulleys');
  assert.equal(timer(completed).textContent, 'Due in 250 printing h');
  assert.match(completed.children[0].children[2].textContent, /Last completed at 1,250 h/);
  assert.equal(action(completed).textContent, 'Undo completion');
  assert.equal(action(completed).focused, true);
  assert.match(elements['maint-feedback'].textContent, /Not saved to the printer/);
  action(completed).listeners.click();
  const restored = rows(elements, 'attention')[0];
  assert.equal(timer(restored).textContent, '25 printing h overdue');
  assert.equal(action(restored).textContent, 'Mark completed');
  assert.equal(action(restored).focused, true);
  assert.match(elements['maint-feedback'].textContent, /Completion undone/);
});

test('early completion also resets a task and all attention tasks can be cleared', () => {
  const elements = mount();
  action(rows(elements, 'upcoming')[0]).listeners.click();
  const completed = rows(elements, 'upcoming').find(row => title(row) === 'Inspect motion components');
  assert.equal(timer(completed).textContent, 'Due in 500 printing h');
  while (rows(elements, 'attention').length) {
    action(rows(elements, 'attention')[0]).listeners.click();
  }
  assert.equal(elements['maint-attention-count'].textContent, '0');
  assert.equal(elements['maint-attention-empty'].hidden, false);
  assert.equal(elements['maint-attention-list'].hidden, true);
  assert.equal(elements['maint-upcoming-count'].textContent, '4');
  elements['maint-reset'].listeners.click();
  assert.equal(rows(elements, 'attention').length, 2);
  assert.equal(timer(rows(elements, 'upcoming')[0]).textContent, 'Due in 25 printing h');
  assert.equal(elements['maint-attention-empty'].hidden, true);
  assert.equal(elements['maint-attention-list'].hidden, false);
  assert.match(elements['maint-feedback'].textContent, /Preview reset/);
  assert.equal(rows(mount(), 'attention').length, 2);
});

test('every task has a labeled completion action and QR guide placeholder', () => {
  const elements = mount();
  for (const row of [...rows(elements, 'attention'), ...rows(elements, 'upcoming')]) {
    assert.equal(action(row).type, 'button');
    assert.ok(action(row).attributes['aria-label'].includes(title(row)));
    const [qr, caption] = row.children[2].children;
    assert.equal(qr.attributes.role, 'img');
    assert.ok(qr.attributes['aria-label'].includes(title(row)));
    assert.match(qr.attributes['aria-label'], /placeholder.*not linked yet/);
    assert.equal(caption.textContent, 'Guide coming soon');
  }
  assert.match(html, /role="status" aria-live="polite"/);
  for (const id of ['maint-preview-title', 'maint-attention-title', 'maint-upcoming-title']) {
    assert.ok(html.includes(`aria-labelledby="${id}"`));
    assert.ok(html.includes(`id="${id}"`));
  }
});

test('responsive styles remain scoped to maintenance', () => {
  assert.match(html, /@media\(max-width:650px\)/);
  assert.match(html, /\.maint-task\{grid-template-columns:minmax\(0,1fr\) 88px/);
  const styles = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  for (const selector of styles.matchAll(/(?:^|})\s*([^{}@]+)\{/g)) {
    assert.ok(selector[1].trim().startsWith('.maint-'), selector[1]);
  }
});
