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

async function mount({ status = printerStatus(), queryResponse, moveResponse, moveWait, onScript,
  onQuery, onTick, onRegister, onSet, initialRegisters = {}, storage = new Map() } = {}){
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
  for (const id of ['belt-run', 'belt-reference', 'belt-stop', 'belt-axis', 'belt-clear', 'out-belt']){
    elements[id] = element();
  }
  elements['belt-axis'].value = 'x';
  elements['belt-clear'].checked = false;
  const requests = [];
  const history = [];
  const registers = { ...initialRegisters };
  let now = 0;
  const context = {
    document: { getElementById: id => elements[id] || null },
    location: { hostname: 'printer.local' },
    localStorage: {
      getItem: key => key === 'moonrakerUrl' ? 'http://configured-printer:7125/' : storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value)
    },
    Date: { now: () => now },
    setTimeout: (callback, delay) => {
      now += delay;
      if (onTick) onTick();
      queueMicrotask(callback);
    },
    fetch: async (url, options) => {
      const payload = options.body ? JSON.parse(options.body) : {};
      requests.push({ url, payload });
      if (url.includes('/server/gcode_store')){
        return { ok: true, status: 200, json: async () => ({ result: { gcode_store: history } }) };
      }
      if (url.endsWith('/printer/objects/query')){
        const custom = onQuery ? onQuery(JSON.parse(options.body).objects) : null;
        return { ok: true, status: 200, json: async () => custom || queryResponse || { result: { status } } };
      }
      if (url.endsWith('/printer/emergency_stop')){
        return { ok: true, status: 200, json: async () => ({ result: 'ok' }) };
      }
      const dump = payload.script.match(/^DUMP_TMC STEPPER=(\w+) REGISTER=(\w+)$/);
      if (dump){
        const key = dump[1] + ':' + dump[2].toLowerCase();
        const value = registers[key] ?? (dump[2] === 'THIGH' ? 17 : 0);
        history.push({ type: 'command', message: payload.script, time: history.length + 1 });
        if (!onRegister || onRegister(dump[1], dump[2], value) !== false){
          history.push({ type: 'response', message: `${dump[2]}: ${value.toString(16).padStart(8, '0')}`, time: history.length + 1 });
        }
        return { ok: true, status: 200, json: async () => ({ result: 'ok' }) };
      }
      if (payload.script.startsWith('SET_TMC_FIELD')){
        const failure = onSet ? onSet(payload.script) : null;
        if (failure) return { ok: true, status: 200, json: async () => ({ error: { message: failure } }) };
        for (const match of payload.script.matchAll(/STEPPER=(\w+) FIELD=(\w+) VALUE=(\d+)/g)){
          registers[match[1] + ':' + match[2]] = Number(match[3]);
          if (match[2] === 'en_pwm_mode'){
            const key = match[1] + ':gconf';
            registers[key] = ((registers[key] ?? 0) & ~4) | (Number(match[3]) << 2);
          }
        }
        return { ok: true, status: 200, json: async () => ({ result: 'ok' }) };
      }
      if (moveWait) await moveWait;
      const scriptResponse = onScript ? await onScript(JSON.parse(options.body).script) : null;
      return { ok: true, status: 200, json: async () => scriptResponse || moveResponse || { result: 'ok' } };
    }
  };
  vm.runInNewContext(script, context);
  await new Promise(resolve => setImmediate(resolve));
  return { points, grid, refresh, start, shimstock, pidGo, out, requests, status, elements, storage, registers };
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

test('start homes unhomed axes before moving to absolute Z 101.3 and waits for completion', async () => {
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
    'SAVE_GCODE_STATE NAME=mconfig_bed_level', 'G90', 'G1 Z101.3 F600',
    'M400', 'RESTORE_GCODE_STATE NAME=mconfig_bed_level'
  ].join('\n'));
  assert.ok(page.points.every(point => !point.disabled));
  assert.equal(page.start.disabled, false);
  assert.equal(page.out.textContent, 'Bed leveling ready at Z 101.3 mm. Select a square to move X/Y.');
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

function beltPrinterStatus(){
  const status = printerStatus();
  Object.assign(status.toolhead, { max_velocity: 300, max_accel: 1000, square_corner_velocity: 5,
    position: [295, 305, 100, 0] });
  status.gcode_move.speed_factor = 1;
  status.motion_report = { live_velocity: 50 };
  status.configfile = { settings: {
    printer: { kinematics: 'cartesian' },
    stepper_x: {}, stepper_y: {}, stepper_y1: {},
    'tmc5160 stepper_x': { run_current: 1.4, stealthchop_threshold: 0 },
    'tmc5160 stepper_y': { run_current: 1.4 },
    'tmc5160 stepper_y1': { run_current: 1.4 }
  } };
  for (const [stepper, value] of [['stepper_x', 0], ['stepper_y', 80], ['stepper_y1', 160]]){
    status['tmc5160 ' + stepper] = { run_current: 1.4, drv_status: {
      cs_actual: 20, ...(value ? { sg_result: value } : {})
    } };
  }
  return status;
}

async function mountBelt({ status = beltPrinterStatus(), ticks = 6, storage, onSample, onMove, onRegister, onSet,
  initialRegisters } = {}){
  let moves = 0;
  let remaining = 0;
  let finish;
  const page = await mount({
    status, storage, onRegister, onSet, initialRegisters,
    onScript: script => {
      moves++;
      if (onMove) onMove(script, moves);
      if (moves % 3 === 1) return null;
      remaining = ticks;
      return new Promise(resolve => {
        finish = resolve;
        // M400 still completes if sampling exits early and stops its timer loop.
        setImmediate(() => {
          if (finish === resolve){
            finish(null);
            finish = null;
          }
        });
      });
    },
    onTick: () => {
      if (finish && --remaining <= 0){
        finish(null);
        finish = null;
      }
    },
    onQuery: objects => {
      if (objects.motion_report && onSample) return onSample(status, page);
      return null;
    }
  });
  page.elements['belt-clear'].checked = true;
  return page;
}

function beltMotionRequests(page){
  return page.requests.filter(request => request.payload.script?.includes('G1 '));
}

test('belt test samples real zero SG_RESULT, makes bounded XY-only sweeps, and saves a reference', async () => {
  const page = await mountBelt();
  page.status.toolhead.axis_minimum[0] = -55;
  page.status.gcode_move.homing_origin[0] = -55;
  await page.elements['belt-run'].click();
  const scripts = beltMotionRequests(page).map(request => request.payload.script);
  assert.equal(scripts.length, 3);
  for (const [index, script] of scripts.entries()){
    assert.equal(script, [
      'SAVE_GCODE_STATE NAME=mconfig_belt', 'G90',
      `G1 X${[129, 516, 129][index]} F3000`, 'M400',
      'RESTORE_GCODE_STATE NAME=mconfig_belt'
    ].join('\n'));
  }
  assert.match(page.elements['out-belt'].textContent, /stepper_x: median 0, range 0-0, 8 samples/);
  assert.equal(page.elements['belt-reference'].disabled, false);
  await page.elements['belt-reference'].click();
  const reference = JSON.parse([...page.storage.values()][0]);
  assert.equal(reference.results.stepper_x.median, 0);
  assert.equal(reference.results.stepper_x.count, 8);
  assert.equal(page.elements['belt-clear'].checked, false);
  assert.equal(page.pidGo.disabled, false);
  assert.equal(page.registers['stepper_x:tcoolthrs'], 0);
  assert.equal(page.registers['stepper_x:thigh'], 17);
});

test('Y test reports both motors separately and compares only matching references', async () => {
  const page = await mountBelt();
  page.elements['belt-axis'].value = 'y';
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /stepper_y: median 80/);
  assert.match(page.elements['out-belt'].textContent, /stepper_y1: median 160/);
  await page.elements['belt-reference'].click();
  const next = await mountBelt({ storage: page.storage });
  next.elements['belt-axis'].value = 'y';
  next.status['tmc5160 stepper_y'].drv_status.sg_result = 60;
  await next.elements['belt-run'].click();
  assert.match(next.elements['out-belt'].textContent, /reference 80, change -20.0/);
  assert.match(next.elements['out-belt'].textContent, /reference 160, change 0.0/);
  next.status.toolhead.max_accel = 900;
  next.elements['belt-clear'].checked = true;
  await next.elements['belt-run'].click();
  assert.match(next.elements['out-belt'].textContent, /Reference settings differ; comparison withheld/);
  assert.doesNotMatch(next.elements['out-belt'].textContent, /change -20/);
  next.status.toolhead.max_accel = 1000;
  next.status.toolhead.position[0] = 100;
  next.elements['belt-clear'].checked = true;
  await next.elements['belt-run'].click();
  assert.match(next.elements['out-belt'].textContent, /Reference settings differ; comparison withheld/);
});

test('belt test refuses unsafe states and unsupported drivers before any motion', async () => {
  const changes = [
    status => { status.toolhead.homed_axes = 'xy'; },
    status => { status.print_stats.state = 'printing'; },
    status => { status.print_stats.state = 'paused'; },
    status => { status.idle_timeout.state = 'Printing'; },
    status => { status.webhooks.state = 'shutdown'; },
    status => { status.configfile.settings.printer.kinematics = 'corexy'; },
    status => { delete status.configfile.settings['tmc5160 stepper_x']; },
    status => { status.configfile.settings['tmc5160 stepper_x'].driver_semin = 1; },
    status => { status.gcode_move.speed_factor = 0.5; },
    status => { status.toolhead.max_velocity = 49; },
    status => { status.toolhead.axis_maximum[0] = 300; },
    status => { delete status['tmc5160 stepper_x'].run_current; }
  ];
  for (const change of changes){
    const status = beltPrinterStatus();
    change(status);
    const page = await mountBelt({ status });
    await page.elements['belt-run'].click();
    assert.equal(beltMotionRequests(page).length, 0);
    assert.match(page.elements['out-belt'].textContent, /^Error:/);
    assert.equal(page.elements['belt-reference'].disabled, true);
  }
  const page = await mountBelt();
  page.elements['belt-clear'].checked = false;
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /Confirm homing and clearance/);
  assert.equal(beltMotionRequests(page).length, 0);
});

test('missing second Y driver, invalid samples, or too few steady-speed readings never produce a result', async () => {
  const cases = [
    status => { delete status.configfile.settings['tmc5160 stepper_y1']; },
    status => { status['tmc5160 stepper_y1'].drv_status = null; },
    status => { status['tmc5160 stepper_y1'].drv_status.stst = 1; },
    status => { status['tmc5160 stepper_y1'].drv_status.stealth = 1; },
    status => { status['tmc5160 stepper_y1'].drv_status.otpw = 1; },
    status => { status['tmc5160 stepper_y1'].drv_status.sg_result = 1024; },
    status => { status.motion_report.live_velocity = 48; }
  ];
  for (const change of cases){
    const status = beltPrinterStatus();
    change(status);
    const page = await mountBelt({ status });
    page.elements['belt-axis'].value = 'y';
    await page.elements['belt-run'].click();
    assert.match(page.elements['out-belt'].textContent, /^Error:/);
    assert.equal(page.elements['belt-reference'].disabled, true);
    assert.doesNotMatch(page.elements['out-belt'].textContent, /Test complete/);
  }
  const page = await mountBelt({ ticks: 2 });
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /Insufficient steady-speed samples/);
});

test('sampling failure waits for the queued move, holds other controls, and does not queue a return move', async () => {
  let checked = false;
  const page = await mountBelt({ onSample: (status, page) => {
    checked = true;
    assert.equal(page.pidGo.disabled, true);
    assert.equal(page.start.disabled, true);
    assert.equal(page.elements['belt-run'].disabled, true);
    return { error: { message: 'Driver query failed' } };
  } });
  await page.elements['belt-run'].click();
  assert.equal(checked, true);
  assert.match(page.elements['out-belt'].textContent, /Driver query failed/);
  assert.equal(beltMotionRequests(page).length, 2);
  assert.equal(page.registers['stepper_x:tcoolthrs'], 0);
  assert.equal(page.registers['stepper_x:thigh'], 17);
  assert.equal(page.pidGo.disabled, false);
});

test('configuration changes and tab removal during the test prevent a successful comparison', async () => {
  const changed = await mountBelt({ onMove: (script, count) => {
    if (count === 2) changed.status['tmc5160 stepper_x'].run_current = 1.2;
  } });
  await changed.elements['belt-run'].click();
  assert.match(changed.elements['out-belt'].textContent, /settings changed during the test/);
  assert.equal(changed.elements['belt-reference'].disabled, true);
  const removed = await mountBelt({ onSample: (status, page) => {
    page.elements['belt-run'].isConnected = false;
    return null;
  } });
  await removed.elements['belt-run'].click();
  assert.match(removed.elements['out-belt'].textContent, /Test tab was closed/);
  assert.equal(beltMotionRequests(removed).length, 2);
  assert.equal(removed.registers['stepper_x:tcoolthrs'], 0);
});

test('emergency stop interrupts the workflow and cannot produce a saved result', async () => {
  let stop;
  const page = await mountBelt({ onSample: (status, page) => {
    if (!stop) stop = page.elements['belt-stop'].click();
    return null;
  } });
  await page.elements['belt-run'].click();
  await stop;
  assert.ok(page.requests.some(request => request.url.endsWith('/printer/emergency_stop')));
  assert.match(page.elements['out-belt'].textContent, /Restart Klipper/);
  assert.equal(beltMotionRequests(page).length, 2);
  assert.equal(page.elements['belt-reference'].disabled, true);
});

test('unavailable register snapshots stop before threshold changes or motion', async () => {
  const page = await mountBelt({ onRegister: () => false });
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /could not capture TCOOLTHRS/);
  assert.equal(beltMotionRequests(page).length, 0);
  assert.deepEqual(page.registers, {});
});

test('a restoration error is explicit, not retried, and prevents reference saving', async () => {
  let restorations = 0;
  const page = await mountBelt({ onSet: script => {
    if (script.includes('FIELD=thigh VALUE=17')){
      restorations++;
      return 'SPI communication failed';
    }
    return null;
  } });
  await page.elements['belt-run'].click();
  assert.equal(restorations, 1);
  assert.match(page.elements['out-belt'].textContent, /Could not restore StallGuard driver settings: SPI communication failed/);
  assert.match(page.elements['out-belt'].textContent, /Restart Klipper before further motion/);
  assert.equal(page.elements['belt-reference'].disabled, true);
  assert.equal(page.storage.size, 0);
});

test('live GCONF 0000000c switches to spreadCycle for motion and restores stealthChop without altering configuration', async () => {
  const status = beltPrinterStatus();
  status.configfile.settings['tmc5160 stepper_x'].stealthchop_threshold = 999999;
  status['tmc5160 stepper_x'].drv_status.stealth = 1;
  const originalConfig = JSON.stringify(status.configfile.settings);
  const page = await mountBelt({
    status, initialRegisters: { 'stepper_x:gconf': 0x0c },
    onMove: () => {
      assert.equal(page.registers['stepper_x:gconf'], 0x08);
      delete status['tmc5160 stepper_x'].drv_status.stealth;
    }
  });
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /Test complete/);
  assert.equal(beltMotionRequests(page).length, 3);
  assert.equal(page.registers['stepper_x:gconf'], 0x0c);
  assert.equal(JSON.stringify(status.configfile.settings), originalConfig);
  assert.match(page.requests.at(-1).payload.script, /FIELD=en_pwm_mode VALUE=1$/);
});

test('a sampling failure restores the original chopper mode after the queued move finishes', async () => {
  const page = await mountBelt({
    initialRegisters: { 'stepper_x:gconf': 0x0c },
    onSample: () => ({ error: { message: 'Driver query failed' } })
  });
  await page.elements['belt-run'].click();
  assert.match(page.elements['out-belt'].textContent, /Driver query failed/);
  assert.equal(beltMotionRequests(page).length, 2);
  assert.equal(page.registers['stepper_x:gconf'], 0x0c);
  assert.equal(page.registers['stepper_x:tcoolthrs'], 0);
  assert.equal(page.elements['belt-reference'].disabled, true);
});

test('live CoolStep and driver stop mode stay blocked with specific errors before changing registers', async () => {
  for (const [register, value, message] of [
    ['coolconf', 1, /live CoolStep is enabled/],
    ['gconf', 0x800c, /live driver stop mode is enabled/]
  ]){
    const initialRegisters = { ['stepper_x:' + register]: value };
    const page = await mountBelt({ initialRegisters });
    await page.elements['belt-run'].click();
    assert.match(page.elements['out-belt'].textContent, message);
    assert.equal(beltMotionRequests(page).length, 0);
    assert.deepEqual(page.registers, initialRegisters);
  }
});
