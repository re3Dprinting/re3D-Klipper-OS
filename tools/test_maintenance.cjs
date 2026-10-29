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
const allRows = elements => [...rows(elements, 'attention'), ...rows(elements, 'upcoming')];
const find = (elements, name) => allRows(elements).find(row => title(row) === name);
const beltTitle = 'Check belt and V-groove wheel tension';

test('maintenance is registered in navigation, deep links, titles, and descriptions', () => {
  assert.match(shell, /class="tab" data-page="maintenance"[^]*?<\/svg>Maintenance<\/button>/);
  assert.ok(shell.match(/const TABS = \[([^\]]+)\]/)[1].includes("'maintenance'"));
  assert.match(shell, /maintenance:'Maintenance'/);
  assert.match(shell, /maintenance: 'Plan routine maintenance/);
});

test('guide tasks use explicitly arbitrary demo hours without live data or persistence', () => {
  assert.match(html, /Demo only/);
  assert.match(html, /all hour intervals and completion readings are arbitrary/);
  assert.match(html, /not approved maintenance guidance/);
  assert.match(html, /not conversions of the guide's calendar intervals/);
  assert.match(html, /calendar, annual, material-change, wear, and usage-based requirements still apply/);
  assert.match(html, /changes reset when you leave or reload/);
  assert.match(html, /Timers use printing hours, not wall-clock time/);
  assert.doesNotMatch(script, /\bfetch\b|localStorage|sessionStorage|setInterval|setTimeout/);
  assert.doesNotMatch(html, /<form\b|<iframe\b/);
});

test('tasks are divided by due threshold and sorted by printing hours remaining', () => {
  const elements = mount();
  const attention = rows(elements, 'attention');
  const upcoming = rows(elements, 'upcoming');
  assert.equal(attention.length, 4);
  assert.equal(upcoming.length, 9);
  assert.equal(timer(attention[0]).textContent, '25 printing h overdue');
  assert.equal(timer(attention[0]).dataset.status, 'overdue');
  assert.equal(timer(find(elements, 'Clean bed')).textContent, 'Due now');
  assert.equal(timer(find(elements, 'Clean bed')).dataset.status, 'due');
  assert.equal(timer(upcoming[0]).textContent, 'Due in 25 printing h');
  assert.equal(timer(upcoming.at(-1)).textContent, 'Due in 750 printing h');
  for (const group of ['attention', 'upcoming']) {
    assert.equal(elements[`maint-${group}-count`].textContent, String(rows(elements, group).length));
    assert.equal(elements[`maint-${group}-empty`].hidden, true);
    assert.equal(elements[`maint-${group}-list`].hidden, false);
  }
});

test('completing a task resets its timer, moves groups, and supports undo with focus', () => {
  const elements = mount();
  action(find(elements, beltTitle)).listeners.click();
  assert.equal(rows(elements, 'attention').length, 3);
  assert.equal(elements['maint-upcoming-count'].textContent, '10');
  const completed = find(elements, beltTitle);
  assert.equal(timer(completed).textContent, 'Due in 250 printing h');
  assert.match(completed.children[0].children[2].textContent, /Last completed at 1,250 h/);
  assert.equal(action(completed).textContent, 'Undo completion');
  assert.equal(action(completed).focused, true);
  assert.match(elements['maint-feedback'].textContent, /Not saved to the printer/);
  action(completed).listeners.click();
  const restored = find(elements, beltTitle);
  assert.equal(timer(restored).textContent, '25 printing h overdue');
  assert.equal(action(restored).textContent, 'Mark completed');
  assert.equal(action(restored).focused, true);
  assert.match(elements['maint-feedback'].textContent, /Completion undone/);
});

test('early completion also resets a task and all attention tasks can be cleared', () => {
  const elements = mount();
  action(rows(elements, 'upcoming')[0]).listeners.click();
  const completed = find(elements, 'Wipe down panels');
  assert.equal(timer(completed).textContent, 'Due in 50 printing h');
  while (rows(elements, 'attention').length) {
    action(rows(elements, 'attention')[0]).listeners.click();
  }
  assert.equal(elements['maint-attention-count'].textContent, '0');
  assert.equal(elements['maint-attention-empty'].hidden, false);
  assert.equal(elements['maint-attention-list'].hidden, true);
  assert.equal(elements['maint-upcoming-count'].textContent, '13');
  elements['maint-reset'].listeners.click();
  assert.equal(rows(elements, 'attention').length, 4);
  assert.equal(timer(rows(elements, 'upcoming')[0]).textContent, 'Due in 25 printing h');
  assert.equal(elements['maint-attention-empty'].hidden, true);
  assert.equal(elements['maint-attention-list'].hidden, false);
  assert.match(elements['maint-feedback'].textContent, /Preview reset/);
  assert.equal(rows(mount(), 'attention').length, 4);
});

test('every task has a labeled completion action and QR guide placeholder', () => {
  const elements = mount();
  for (const row of [...rows(elements, 'attention'), ...rows(elements, 'upcoming')]) {
    assert.equal(action(row).type, 'button');
    assert.ok(action(row).attributes['aria-label'].includes(title(row)));
    const [qr, caption] = row.children[2].children;
    assert.equal(qr.attributes.role, 'img');
    assert.ok(qr.attributes['aria-label'].includes(title(row)));
    assert.match(qr.attributes['aria-label'], /placeholder.*Not scannable yet/);
    assert.ok(['Open guide', 'Guide not linked'].includes(caption.textContent));
  }
  assert.match(html, /role="status" aria-live="polite"/);
  for (const id of ['maint-preview-title', 'maint-attention-title', 'maint-upcoming-title']) {
    assert.ok(html.includes(`aria-labelledby="${id}"`));
    assert.ok(html.includes(`id="${id}"`));
  }
});

test('all 13 guide tasks retain source cadence, task notes, and placeholder intervals', () => {
  const elements = mount();
  const expected = [
    ['Clean rails / V-groove wheels', 50, 'Weekly'],
    ['Clean bed', 50, 'Weekly'],
    ['Wipe down panels', 50, 'Weekly'],
    ['Level the bed', 250, 'Monthly'],
    ['Level the nozzles', 250, 'Monthly'],
    ['Adjust the Z-gap', 250, 'Monthly'],
    [beltTitle, 250, 'Monthly'],
    ['Inspect bot for wear and tear', 250, 'Monthly'],
    ['Update firmware and back up configuration files', 1000, 'Quarterly / as needed'],
    ['Replace worn PrintinZ / check heater pad adhesion', 1000, 'Quarterly / as needed'],
    ['Re-grease linear rails', 1000, 'Quarterly / as needed'],
    ['Re-grease Z-rods', 1000, 'Quarterly / as needed'],
    ['Clean the nozzles', 1000, 'Quarterly / as needed']
  ];
  assert.equal(allRows(elements).length, expected.length);
  for (const [name, interval, cadence] of expected) {
    const row = find(elements, name);
    assert.ok(row, name);
    const meta = row.children[0].children[2].textContent;
    assert.ok(meta.includes(`Demo: every ${interval.toLocaleString('en-US')} printing h`));
    assert.ok(meta.includes(`Guide: ${cadence}`));
    assert.ok(row.children[0].children[3].textContent.startsWith('Guide:'));
  }
  assert.match(find(elements, 'Re-grease linear rails').children[0].children[3].textContent,
    /1,200-1,500 printing hours or annually, whichever comes first/);
  assert.match(find(elements, 'Re-grease Z-rods').children[0].children[3].textContent,
    /1-2 months.*3-6 months/);
});

test('five resource-sheet URLs are attached only to their corresponding tasks', () => {
  const elements = mount();
  const expected = new Map([
    ['Level the bed', 'https://bit.ly/bedleveling2'],
    ['Level the nozzles', 'https://bit.ly/levelingnozzles'],
    ['Adjust the Z-gap', 'https://bit.ly/zaxishome'],
    [beltTitle, 'https://bit.ly/beltandwheeltension'],
    ['Update firmware and back up configuration files', 'https://bit.ly/klipperfirmwareinstal']
  ]);
  for (const row of allRows(elements)) {
    const caption = row.children[2].children[1];
    if (expected.has(title(row))) {
      assert.equal(caption.href, expected.get(title(row)));
      assert.equal(caption.target, '_blank');
      assert.equal(caption.rel, 'noopener noreferrer');
      assert.equal(caption.attributes['aria-label'], `Open guide: ${title(row)}`);
    } else {
      assert.equal(caption.textContent, 'Guide not linked');
      assert.equal(caption.href, undefined);
    }
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
