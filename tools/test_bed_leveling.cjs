const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const tabs = path.join(__dirname,
  '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig', 'www',
  'tabs');
const html = fs.readFileSync(path.join(tabs, 'tuning.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function printerStatus(){
  return {
    toolhead: { homed_axes: 'xyz', axis_minimum: [0, 0, 0], axis_maximum: [590, 610, 602] },
    gcode_move: { homing_origin: [0, 0, 0, 0] },
    webhooks: { state: 'ready' },
    print_stats: { state: 'standby' },
    idle_timeout: { state: 'Ready' }
  };
}

function element(){
  return {
    disabled: true, isConnected: true, textContent: '', attributes: {}, listeners: {},
    setAttribute(name, value){ this.attributes[name] = value; },
    addEventListener(name, callback){ this.listeners[name] = callback; },
    click(){ return this.listeners.click(); }
  };
}

async function mount({ status = printerStatus(), queryResponse, moveResponse, moveWait, onScript } = {}){
  const points = [...html.matchAll(/class="bed-level-point" data-x="(\d)" data-y="(\d)"[^>]*>(.*?)<\/button>/g)]
    .map((match) => {
      const point = element();
      point.dataset = { x: match[1], y: match[2] };
      point.textContent = match[3];
      return point;
    });
  const grid = element();
  grid.querySelectorAll = () => points;
  const refresh = element();
  const start = element();
  const shimstock = element();
  const pidGo = element();
  pidGo.disabled = false;
  const out = element();
  const elements = {
    'bed-level-grid': grid, 'bed-level-refresh': refresh, 'bed-level-start': start,
    'bed-level-shimstock': shimstock, 'out-bed-level': out, 'pid-go': pidGo
  };
  const requests = [];
  const context = {
    document: { getElementById: id => elements[id] || null },
    location: { hostname: 'printer.local' },
    localStorage: { getItem: () => 'http://configured-printer:7125/' },
    fetch: async (url, options) => {
      requests.push({ url, payload: JSON.parse(options.body) });
      if (url.endsWith('/printer/objects/query')){
        return { ok: true, status: 200, json: async () => queryResponse || { result: { status } } };
      }
      if (moveWait) await moveWait;
      const scriptResponse = onScript ? await onScript(JSON.parse(options.body).script) : null;
      return { ok: true, status: 200, json: async () => scriptResponse || moveResponse || { result: 'ok' } };
    }
  };
  vm.runInNewContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  return { points, grid, refresh, start, shimstock, pidGo, out, requests, status };
}

test('bed leveling is in Tuning only and PID tuning remains present', () => {
  assert.match(html, /id="bed-level-grid"/);
  assert.match(html, /id="pid-form"/);
  assert.match(html, /id="pid-go"/);
  assert.doesNotMatch(fs.readFileSync(path.join(tabs, 'calibration.html'), 'utf8'), /bed-level/);
  for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)){
    assert.doesNotThrow(() => new vm.Script(match[1]));
  }
});

test('renders nine front/rear-oriented points with position names only', async () => {
  const page = await mount();
  assert.equal(page.points.length, 9);
  assert.deepEqual(page.points.map(point => point.textContent), [
    'Rear left', 'Rear center', 'Rear right',
    'Middle left', 'Center', 'Middle right',
    'Front left', 'Front center', 'Front right'
  ]);
  assert.ok(page.points.every(point => !point.disabled));
  assert.equal(page.requests[0].url, 'http://configured-printer:7125/printer/objects/query');
  assert.equal(page.requests.length, 1, 'loading must not move or home');
});

test('all nine squares send XY-only moves and restore G-code state without moving', async () => {
  const page = await mount();
  for (const point of page.points){
    await point.click();
    const request = page.requests.at(-1);
    assert.ok(request.url.endsWith('/printer/gcode/script'));
    const x = [59, 295, 531][Number(point.dataset.x)];
    const y = [61, 305, 549][Number(point.dataset.y)];
    assert.equal(request.payload.script, [
      'SAVE_GCODE_STATE NAME=mconfig_bed_level', 'G90',
      `G1 X${x} Y${y} F3000`, 'M400',
      'RESTORE_GCODE_STATE NAME=mconfig_bed_level'
    ].join('\n'));
    assert.equal(page.points.filter(button => button.attributes['aria-pressed'] === 'true').length, 1);
    assert.match(page.out.textContent, /Move complete/);
  }
});

test('uses fresh limits and offsets for each move, including negative axis minima', async () => {
  const page = await mount();
  page.status.toolhead.axis_minimum[0] = -55;
  page.status.toolhead.axis_maximum = [955, 915, 1000];
  page.status.gcode_move.homing_origin = [-55, -2, 0, 0];
  await page.points[0].click();
  assert.match(page.requests.at(-1).payload.script, /G1 X101 Y825.5 F3000/);
});

test('blocks unhomed, printing, paused, busy, disconnected and malformed states', async () => {
  const cases = [
    status => { status.toolhead.homed_axes = 'xy'; },
    status => { status.print_stats.state = 'printing'; },
    status => { status.print_stats.state = 'paused'; },
    status => { status.idle_timeout.state = 'Printing'; },
    status => { status.webhooks.state = 'shutdown'; },
    status => { delete status.print_stats; },
    status => { status.toolhead.axis_maximum[0] = null; },
    status => { status.toolhead.axis_maximum[0] = 0; },
    status => { status.gcode_move.homing_origin = []; }
  ];
  for (const change of cases){
    const page = await mount();
    change(page.status);
    await page.points[4].click();
    assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 0);
    assert.match(page.out.textContent, /^Error:/);
    assert.ok(page.points.every(point => point.disabled));
    assert.equal(page.refresh.disabled, false);
  }
});

test('reports Moonraker query and command errors without success or automatic retry', async () => {
  const queryFailure = await mount({ queryResponse: { error: { message: 'Klippy disconnected' } } });
  assert.equal(queryFailure.out.textContent, 'Error: Klippy disconnected');
  assert.ok(queryFailure.points.every(point => point.disabled));
  const moveFailure = await mount({ moveResponse: { error: { message: 'Move out of range' } } });
  await moveFailure.points[0].click();
  assert.equal(moveFailure.out.textContent, 'Error: Move out of range');
  assert.ok(moveFailure.points.every(point => point.disabled));
  assert.equal(moveFailure.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 1);
});

test('serializes movement and waits for completion before enabling buttons', async () => {
  let finishMove;
  const moveWait = new Promise(resolve => { finishMove = resolve; });
  const page = await mount({ moveWait });
  const moving = page.points[0].click();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(page.points.every(point => point.disabled));
  assert.equal(page.refresh.disabled, true);
  assert.equal(page.start.disabled, true);
  assert.equal(page.shimstock.disabled, true);
  assert.equal(page.pidGo.disabled, true);
  await page.points[8].click();
  assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 1);
  finishMove();
  await moving;
  assert.ok(page.points.every(point => !point.disabled));
  assert.equal(page.pidGo.disabled, false);
});

test('does not send movement after navigating away during a status request', async () => {
  const page = await mount();
  page.grid.isConnected = false;
  await page.points[0].click();
  assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 0);
});

test('shimstock moves only Z to absolute 0.3, without homing, and preserves the selected square', async () => {
  const page = await mount();
  await page.points[4].click();
  await page.shimstock.click();
  assert.equal(page.requests.at(-1).payload.script, [
    'SAVE_GCODE_STATE NAME=mconfig_bed_level', 'G90', 'G1 Z0.3 F600',
    'M400', 'RESTORE_GCODE_STATE NAME=mconfig_bed_level'
  ].join('\n'));
  assert.equal(page.points[4].attributes['aria-pressed'], 'true');
  assert.equal(page.out.textContent, 'Shimstock test ready at Z 0.3 mm. Raise Z before moving X/Y.');
  assert.equal(page.shimstock.disabled, false);
});

test('shimstock blocks unhomed axes, busy printers, and out-of-range Z with offsets', async () => {
  for (const change of [
    status => { status.toolhead.homed_axes = 'xy'; },
    status => { status.print_stats.state = 'printing'; },
    status => { status.print_stats.state = 'paused'; },
    status => { status.idle_timeout.state = 'Printing'; },
    status => { status.toolhead.axis_minimum[2] = 1; },
    status => { status.gcode_move.homing_origin[2] = -0.4; },
    status => { status.gcode_move.homing_origin[2] = 602; },
    status => { status.toolhead.axis_maximum[2] = null; }
  ]){
    const page = await mount();
    change(page.status);
    await page.shimstock.click();
    assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 0);
    assert.match(page.out.textContent, /^Error:/);
    assert.equal(page.shimstock.disabled, true);
  }
});

test('shimstock command failure leaves movement controls disabled and shows the error', async () => {
  const page = await mount({ moveResponse: { error: { message: 'Z move failed' } } });
  await page.shimstock.click();
  assert.equal(page.out.textContent, 'Error: Z move failed');
  assert.equal(page.shimstock.disabled, true);
  assert.ok(page.points.every(point => point.disabled));
});

test('shimstock serializes with other actions and waits for Z movement to complete', async () => {
  let finishMove;
  const moveWait = new Promise(resolve => { finishMove = resolve; });
  const page = await mount({ moveWait });
  const moving = page.shimstock.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.shimstock.disabled, true);
  assert.equal(page.start.disabled, true);
  assert.equal(page.refresh.disabled, true);
  assert.ok(page.points.every(point => point.disabled));
  await page.start.click();
  await page.points[4].click();
  assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 1);
  finishMove();
  await moving;
  assert.equal(page.shimstock.disabled, false);
  assert.ok(page.points.every(point => !point.disabled));
});

test('start homes unhomed axes before moving to absolute Z 101.6 and waits for completion', async () => {
  const status = printerStatus();
  status.toolhead.homed_axes = '';
  const page = await mount({ status, onScript: script => {
    if (script.startsWith('G28')){
      status.toolhead.homed_axes = 'xyz';
      status.idle_timeout.state = 'Printing';
    }
  } });
  await page.start.click();
  const commands = page.requests.filter(request => request.url.endsWith('/printer/gcode/script'));
  assert.equal(commands.length, 2);
  assert.equal(commands[0].payload.script, 'G28\nM400');
  assert.equal(commands[1].payload.script, [
    'SAVE_GCODE_STATE NAME=mconfig_bed_level', 'G90', 'G1 Z101.6 F600',
    'M400', 'RESTORE_GCODE_STATE NAME=mconfig_bed_level'
  ].join('\n'));
  assert.ok(page.points.every(point => !point.disabled));
  assert.equal(page.start.disabled, false);
  assert.equal(page.out.textContent, 'Bed leveling ready at Z 101.6 mm. Select a square to move X/Y.');
  status.idle_timeout.state = 'Ready';
  await page.points[4].click();
  assert.match(page.requests.at(-1).payload.script, /G1 X295 Y305 F3000/);
});

test('start blocks printing, paused, busy and invalid Z limits before homing', async () => {
  for (const change of [
    status => { status.print_stats.state = 'printing'; },
    status => { status.print_stats.state = 'paused'; },
    status => { status.idle_timeout.state = 'Printing'; },
    status => { status.toolhead.axis_maximum[2] = 100; },
    status => { status.toolhead.axis_minimum[2] = 102; },
    status => { status.toolhead.axis_maximum[2] = null; },
    status => { status.gcode_move.homing_origin[2] = 501; }
  ]){
    const page = await mount();
    change(page.status);
    await page.start.click();
    assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 0);
    assert.match(page.out.textContent, /^Error:/);
    assert.ok(page.points.every(point => point.disabled));
  }
});

test('failed homing does not send a Z move or enable the grid', async () => {
  const page = await mount({ moveResponse: { error: { message: 'Homing failed' } } });
  await page.start.click();
  assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 1);
  assert.equal(page.out.textContent, 'Error: Homing failed');
  assert.ok(page.points.every(point => point.disabled));
});

test('rechecks homed axes and Z offsets after homing before moving Z', async () => {
  for (const afterHome of [
    status => { status.toolhead.homed_axes = 'xy'; },
    status => { status.gcode_move.homing_origin[2] = 501; },
    status => { status.print_stats.state = 'printing'; }
  ]){
    const status = printerStatus();
    const page = await mount({ status, onScript: script => {
      if (script.startsWith('G28')) afterHome(status);
    } });
    await page.start.click();
    assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 1);
    assert.match(page.out.textContent, /^Error:/);
    assert.ok(page.points.every(point => point.disabled));
  }
});

test('Z move failure is shown explicitly and leaves the grid disabled', async () => {
  const page = await mount({ onScript: script => {
    if (script.includes('G1 Z')) return { error: { message: 'Z move failed' } };
  } });
  await page.start.click();
  assert.equal(page.out.textContent, 'Error: Z move failed');
  assert.ok(page.points.every(point => point.disabled));
  assert.equal(page.start.disabled, false);
});

test('refresh recovers after homing without sending movement', async () => {
  const status = printerStatus();
  status.toolhead.homed_axes = '';
  const page = await mount({ status });
  assert.ok(page.points.every(point => point.disabled));
  status.toolhead.homed_axes = 'xyz';
  await page.refresh.click();
  assert.ok(page.points.every(point => !point.disabled));
  assert.equal(page.requests.filter(request => request.url.endsWith('/printer/gcode/script')).length, 0);
});
