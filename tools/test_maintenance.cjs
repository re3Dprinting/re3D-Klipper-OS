const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const root = path.join(__dirname, '..', 'src', 'modules', 'fullpageos',
  'filesystem', 'opt', 'mconfig', 'www');
const shell = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const html = fs.readFileSync(path.join(root, 'tabs', 'maintenance.html'), 'utf8');

test('maintenance is registered in navigation, deep links, titles, and descriptions', () => {
  assert.match(shell, /class="tab" data-page="maintenance"[^]*?<\/svg>Maintenance<\/button>/);
  const tabs = shell.match(/const TABS = \[([^\]]+)\]/)[1];
  assert.ok(tabs.includes("'maintenance'"));
  assert.match(shell, /maintenance:'Maintenance'/);
  assert.match(shell, /maintenance: 'Plan routine maintenance/);
});

test('template clearly labels examples and cannot read or change printer state', () => {
  assert.match(html, /Preview only/);
  assert.match(html, /not live printer data or approved maintenance guidance/);
  assert.match(html, /Live hour tracking and saving completed tasks are not available yet/);
  assert.doesNotMatch(html, /<script\b|<form\b|<button\b|<input\b|<iframe\b/i);
});

test('sample task intervals and statuses agree with the overview printing hours', () => {
  const hours = Number(html.match(/<dd>([\d,]+) h<\/dd>/)[1].replaceAll(',', ''));
  const tasks = [...html.matchAll(/<li class="maint-task">([\s\S]*?)<\/li>/g)];
  assert.equal(tasks.length, 4);
  const statuses = [];
  const futureDue = [];
  for (const [, task] of tasks) {
    const readings = [...task.matchAll(/<dd>([\d,]+)(?: printing)? h<\/dd>/g)]
      .map(match => Number(match[1].replaceAll(',', '')));
    assert.equal(readings.length, 4);
    const [interval, last, next, elapsed] = readings;
    assert.equal(last + interval, next);
    assert.equal(hours - last, elapsed);
    const [, status, label] = task.match(/data-status="([^"]+)">([^<]+)</);
    statuses.push(status);
    if (next < hours) {
      assert.equal(status, 'overdue');
      assert.equal(label, `Overdue by ${hours - next} h`);
    } else if (next === hours) {
      assert.equal(status, 'due');
      assert.equal(label, 'Due now');
    } else {
      assert.ok(['soon', 'scheduled'].includes(status));
      assert.equal(label, `Due in ${next - hours} h`);
      futureDue.push(next - hours);
    }
  }
  assert.deepEqual(statuses, ['overdue', 'due', 'soon', 'scheduled']);
  const attention = statuses.filter(status => ['overdue', 'due'].includes(status)).length;
  assert.ok(html.includes(`<dd>${attention} tasks</dd>`));
  assert.ok(html.includes(`<dd>In ${Math.min(...futureDue)} h</dd>`));
});

test('template has accessible sections and scoped responsive styles', () => {
  for (const id of ['maint-preview-title', 'maint-schedule-title']) {
    assert.ok(html.includes(`aria-labelledby="${id}"`));
    assert.ok(html.includes(`id="${id}"`));
  }
  assert.match(html, /<ul class="maint-tasks">/);
  assert.equal((html.match(/<h3>/g) || []).length, 4);
  assert.match(html, /@media\(max-width:650px\)/);
  assert.match(html, /\.maint-overview\{grid-template-columns:1fr/);
  assert.match(html, /\.maint-task dl\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  const styles = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  for (const selector of styles.matchAll(/(?:^|})\s*([^{}@]+)\{/g)) {
    assert.ok(selector[1].trim().startsWith('.maint-'), selector[1]);
  }
});
