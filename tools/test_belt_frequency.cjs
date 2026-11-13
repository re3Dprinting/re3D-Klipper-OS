const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const www = path.join(__dirname, '..', 'src', 'modules', 'fullpageos', 'filesystem', 'opt', 'mconfig', 'www');
const script = fs.readFileSync(path.join(www, 'belt-frequency.js'), 'utf8');
const { detectFrequency } = require(path.join(www, 'belt-frequency.js'));
const storageKey = 'mconfig.belt-frequency:https://printer.local:8443';

function signal(sampleRate, frequency, { harmonics = [1], decay = 0, noise = 0, amplitude = 0.2 } = {}){
  let seed = 17;
  return Float32Array.from({ length: 32768 }, (_, index) => {
    const time = index / sampleRate;
    let value = 0;
    harmonics.forEach((weight, harmonic) => {
      value += weight * Math.sin(2 * Math.PI * frequency * (harmonic + 1) * time + 0.7);
    });
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return amplitude * value * (decay ? Math.exp(-time / decay) : 1) + noise * (seed / 2 ** 31 - 1);
  });
}

test('frequency error stays within 1 Hz across phone sample rates and the measurement band', () => {
  for (const sampleRate of [44100, 48000]){
    for (const frequency of [20, 25, 43.7, 86.7, 123.4, 275, 300, 380, 400]){
      const result = detectFrequency(signal(sampleRate, frequency), sampleRate);
      assert.notEqual(result.frequency, null, `${sampleRate} Hz input, ${frequency} Hz belt`);
      assert.ok(Math.abs(result.frequency - frequency) <= 1,
        `${sampleRate} Hz input, ${frequency} Hz belt estimated as ${result.frequency}`);
    }
  }
});

test('decaying plucks and a dominant second harmonic retain the fundamental, not its octave', () => {
  for (const sampleRate of [44100, 48000]){
    for (const frequency of [28.3, 62.8, 140.3, 300]){
      for (const harmonics of [[1], [0.2, 1], [0.12, 1], [0.2, 1, 0.5]]){
        const result = detectFrequency(signal(sampleRate, frequency, {
          harmonics, decay: 0.4, noise: 0.001, amplitude: 0.5
        }), sampleRate);
        assert.notEqual(result.frequency, null, `${frequency} Hz, harmonics ${harmonics}`);
        assert.ok(Math.abs(result.frequency - frequency) <= 1,
          `${frequency} Hz, harmonics ${harmonics}: got ${result.frequency}`);
      }
    }
  }
});

test('silence, white noise, weak input and out-of-band tones do not produce belt readings', () => {
  const cases = [
    new Float32Array(32768),
    new Float32Array(32768).fill(0.3),
    signal(48000, 80, { amplitude: 0.0005 }),
    signal(48000, 80, { amplitude: 0, noise: 0.2 }),
    signal(48000, 10),
    signal(48000, 500),
    signal(48000, 600),
    signal(48000, 1000)
  ];
  cases.forEach(samples => assert.equal(detectFrequency(samples, 48000).frequency, null));
  const invalid = new Float32Array(32768);
  invalid[20000] = NaN;
  assert.throws(() => detectFrequency(invalid, 48000), /invalid audio samples/);
});

function element(){
  const listeners = new Map();
  return {
    disabled: false, checked: false, isConnected: true, value: '', textContent: '', children: [],
    addEventListener(name, handler){ listeners.set(name, handler); },
    removeEventListener(name){ listeners.delete(name); },
    async fire(name){ return listeners.get(name)?.(); },
    setAttribute(){},
    append(...children){ this.children.push(...children); },
    replaceChildren(){ this.children = []; },
    listeners
  };
}

function mount(options = {}){
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const controls = ['pid-go', 'bed-level-start', 'bed-level-refresh', 'bed-level-shimstock', 'bed-point', 'belt-run'].map(get);
  get('bed-point').disabled = true;
  get('frequency-belt').value = 'stepper_y';
  get('frequency-stop').disabled = true;
  get('frequency-save').disabled = true;
  get('frequency-value').textContent = '-- Hz';
  const timers = new Set();
  const tracks = [];
  const contexts = [];
  const requests = [];
  const observers = [];
  const storage = options.storage || new Map();
  let now = 0;
  let input = new Float32Array(32768);
  class Context {
    constructor(){
      this.state = 'suspended';
      this.sampleRate = 48000;
      this.connections = [];
      contexts.push(this);
    }
    async resume(){ this.resumed = true; this.state = 'running'; }
    async close(){ this.state = 'closed'; }
    createMediaStreamSource(){
      return { connect: node => this.connections.push(node), disconnect(){} };
    }
    createAnalyser(){
      return { fftSize: 2048, getFloatTimeDomainData: buffer => buffer.set(input), disconnect(){} };
    }
  }
  const document = Object.assign(element(), {
    visibilityState: 'visible', body: {},
    getElementById: get, createElement: element, querySelectorAll: () => controls
  });
  const window = Object.assign(element(), {
    isSecureContext: options.secure !== false,
    location: { href: 'https://printer.local:8443/#tuning', origin: 'https://printer.local:8443' },
    setInterval(callback){ timers.add(callback); return callback; },
    clearInterval(callback){ timers.delete(callback); }
  });
  window[options.safari ? 'webkitAudioContext' : 'AudioContext'] = Context;
  const navigator = {
    mediaDevices: { async getUserMedia(constraints){
      requests.push(constraints);
      if (options.denied) throw Object.assign(new Error('Denied'), { name: 'NotAllowedError' });
      const track = Object.assign(element(), { stop(){ this.stopped = true; } });
      tracks.push(track);
      const stream = { getTracks: () => [track] };
      if (options.permission) await options.permission;
      return stream;
    } }
  };
  class Observer {
    constructor(callback){ this.callback = callback; observers.push(this); }
    observe(){}
    disconnect(){ this.disconnected = true; }
  }
  class Clock extends Date {
    constructor(...args){ super(...(args.length ? args : [now])); }
    static now(){ return now; }
  }
  vm.runInNewContext(script, {
    document, window, navigator, MutationObserver: Observer, URL, Date: Clock, console,
    localStorage: {
      getItem: name => storage.get(name) ?? null,
      setItem(name, value){
        if (options.storageFailure) throw new Error('Storage full');
        storage.set(name, value);
      }
    }
  });
  return {
    get, document, window, contexts, requests, tracks, timers, storage, observers,
    async start(){ get('frequency-stopped').checked = true; await get('frequency-start').fire('click'); },
    tick(samples){
      if (samples) input = samples;
      now += 100;
      for (const callback of timers) callback();
    },
    capture(frequency){
      for (let index = 0; index < 11; index++) this.tick(new Float32Array(32768));
      for (let index = 0; index < 4; index++) this.tick(signal(48000, frequency));
    }
  };
}

test('microphone requests are explicit, use raw audio constraints and unlock Safari audio', async () => {
  const ui = mount({ safari: true });
  assert.equal(ui.requests.length, 0);
  await ui.get('frequency-start').fire('click');
  assert.equal(ui.requests.length, 0);
  assert.match(ui.get('frequency-status').textContent, /Confirm/);
  await ui.start();
  assert.equal(ui.contexts[0].resumed, true);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.requests[0])), {
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false
  });
  assert.equal(ui.contexts[0].connections.length, 1, 'microphone connects only to analyser, never speakers');
  assert.equal(ui.get('pid-go').disabled, true);
  await ui.get('frequency-stop').fire('click');
  assert.equal(ui.tracks[0].stopped, true);
  assert.equal(ui.contexts[0].state, 'closed');
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.get('pid-go').disabled, false);
  assert.equal(ui.get('bed-point').disabled, true, 'restore the original disabled state');
});

test('HTTP, denied permission and a running printer action produce explicit errors without capture', async () => {
  const http = mount({ secure: false });
  await http.start();
  assert.equal(http.requests.length, 0);
  assert.equal(http.contexts.length, 0);
  assert.match(http.get('frequency-status').textContent, /trusted HTTPS/);
  assert.equal(http.get('frequency-https-link').href, 'https://printer.local:8443/#tuning');
  const denied = mount({ denied: true });
  await denied.start();
  assert.match(denied.get('frequency-status').textContent, /denied or blocked/);
  assert.equal(denied.contexts[0].state, 'closed');
  assert.equal(denied.get('pid-go').disabled, false);
  const busy = mount();
  busy.get('pid-go').disabled = true;
  await busy.start();
  assert.equal(busy.requests.length, 0);
  assert.match(busy.get('frequency-status').textContent, /action is running/);
});

test('stopping during a pending permission prompt also stops the late microphone stream', async () => {
  let grant;
  const ui = mount({ permission: new Promise(resolve => { grant = resolve; }) });
  const pending = ui.start();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.requests.length, 1);
  await ui.get('frequency-stop').fire('click');
  grant();
  await pending;
  assert.equal(ui.tracks[0].stopped, true);
  assert.equal(ui.contexts[0].state, 'closed');
  assert.equal(ui.timers.size, 0);
});

test('hiding, tab removal and page navigation release capture; returning from pagehide can restart', async () => {
  for (const event of ['visibility', 'remove', 'pagehide', 'ended']){
    const ui = mount();
    await ui.start();
    if (event === 'visibility'){
      ui.document.visibilityState = 'hidden';
      await ui.document.fire('visibilitychange');
    } else if (event === 'remove'){
      ui.get('belt-frequency').isConnected = false;
      ui.observers[0].callback();
    } else if (event === 'ended'){
      await ui.tracks[0].fire('ended');
    } else {
      await ui.window.fire('pagehide');
    }
    assert.equal(ui.tracks[0].stopped, true, event);
    assert.equal(ui.contexts[0].state, 'closed', event);
    assert.equal(ui.timers.size, 0, event);
    if (event === 'remove'){
      assert.equal(ui.observers[0].disconnected, true);
      assert.equal(ui.document.listeners.has('visibilitychange'), false);
      assert.equal(ui.window.listeners.has('pagehide'), false);
    }
    if (event === 'pagehide'){
      await ui.start();
      assert.equal(ui.requests.length, 2);
      await ui.get('frequency-stop').fire('click');
    }
  }
});

test('stable plucks save separate belts, survive reload, and do not relabel a prior belt reading', async () => {
  const ui = mount();
  await ui.start();
  for (let index = 0; index < 11; index++) ui.tick();
  ui.tick(signal(48000, 62.8));
  assert.equal(ui.get('frequency-save').disabled, true, 'one frame is not stable');
  ui.tick(signal(48000, 62.8));
  ui.tick(signal(48000, 62.8));
  assert.equal(ui.get('frequency-value').textContent, '62.8 Hz');
  await ui.get('frequency-save').fire('click');
  ui.get('frequency-belt').value = 'stepper_y1';
  await ui.get('frequency-belt').fire('change');
  assert.equal(ui.get('frequency-save').disabled, true);
  assert.equal(ui.get('frequency-value').textContent, '-- Hz');
  assert.equal(ui.tracks[0].stopped, true, 'old belt audio cannot be relabeled as the newly selected belt');
  await ui.start();
  ui.capture(86.7);
  await ui.get('frequency-stop').fire('click');
  assert.equal(ui.get('frequency-value').textContent, '86.7 Hz');
  await ui.get('frequency-save').fire('click');
  const stored = JSON.parse(ui.storage.get(storageKey));
  assert.equal(stored.stepper_y.frequency, 62.8);
  assert.equal(stored.stepper_y1.frequency, 86.7);
  assert.equal(typeof stored.stepper_y.testedAt, 'string');
  const reloaded = mount({ storage: ui.storage });
  assert.equal(reloaded.requests.length, 0);
  assert.equal(reloaded.get('frequency-results').children.length, 2);
  assert.equal(reloaded.get('frequency-results').children[0].children[1].textContent, '62.8 Hz');
});

test('ambient sound and unstable pitches are not saved; interruption and storage failures are visible', async () => {
  const ui = mount({ storageFailure: true });
  await ui.start();
  for (let index = 0; index < 15; index++) ui.tick(signal(48000, 50));
  assert.equal(ui.get('frequency-save').disabled, true, 'no pluck rise above calibrated ambience');
  await ui.get('frequency-stop').fire('click');
  await ui.start();
  for (let index = 0; index < 11; index++) ui.tick(new Float32Array(32768));
  for (const frequency of [70, 90, 70, 90]) ui.tick(signal(48000, frequency));
  assert.equal(ui.get('frequency-save').disabled, true);
  for (let index = 0; index < 4; index++) ui.tick(signal(48000, 70));
  await ui.get('frequency-save').fire('click');
  assert.match(ui.get('frequency-saved-status').textContent, /Error saving.*Storage full/);
  assert.equal(ui.get('frequency-results').children.length, 1);
  ui.contexts[1].state = 'interrupted';
  ui.tick();
  assert.match(ui.get('frequency-status').textContent, /interrupted/);
  assert.equal(ui.tracks[1].stopped, true);
});

test('invalid saved data reports a load error rather than presenting a measurement', () => {
  const ui = mount({ storage: new Map([[storageKey, '{"stepper_y":{"frequency":0,"testedAt":"bad"}}']]) });
  assert.match(ui.get('frequency-saved-status').textContent, /Error loading/);
  assert.equal(ui.get('frequency-results').children.length, 0);
});
