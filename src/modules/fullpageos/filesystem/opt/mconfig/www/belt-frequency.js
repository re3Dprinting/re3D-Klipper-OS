(() => {
  const MIN_HZ = 20;
  const MAX_HZ = 400;

  function detectFrequency(input, sampleRate){
    if (!input || !Number.isInteger(input.length) || input.length < 2 ||
        !Number.isFinite(sampleRate) || sampleRate <= 0){
      throw new Error('Invalid microphone sample buffer or sample rate.');
    }
    const stride = Math.max(1, Math.floor(sampleRate / 8000));
    const rate = sampleRate / stride;
    const count = Math.min(4096, Math.floor(input.length / stride));
    const maxLag = Math.floor(rate / MIN_HZ);
    if (count <= maxLag * 2){
      throw new Error('Microphone sample window is too short for belt-frequency detection.');
    }
    const samples = new Float64Array(count);
    const offset = input.length - count * stride;
    let mean = 0;
    for (let index = 0; index < count; index++){
      let sum = 0;
      for (let part = 0; part < stride; part++) sum += input[offset + index * stride + part];
      samples[index] = sum / stride;
      mean += samples[index];
    }
    mean /= count;
    let energy = 0;
    for (let index = 0; index < count; index++){
      samples[index] -= mean;
      energy += samples[index] * samples[index];
    }
    const rms = Math.sqrt(energy / count);
    if (!Number.isFinite(rms)) throw new Error('Microphone returned invalid audio samples.');
    const empty = { frequency: null, clarity: 0, rms };
    if (rms < 0.002) return empty;

    const difference = new Float64Array(maxLag + 2);
    const normalized = new Float64Array(maxLag + 2);
    let total = 0;
    const windowSize = count - maxLag - 1;
    for (let lag = 1; lag <= maxLag + 1; lag++){
      let sum = 0;
      for (let index = 0; index < windowSize; index++){
        const delta = samples[index] - samples[index + lag];
        sum += delta * delta;
      }
      difference[lag] = sum;
      total += sum;
      normalized[lag] = total > 0 ? sum * lag / total : 1;
    }
    function refine(lag){
      const before = difference[lag - 1];
      const current = difference[lag];
      const after = difference[lag + 1];
      const denominator = before - 2 * current + after;
      const shift = denominator ? Math.max(-0.5, Math.min(0.5, (before - after) / (2 * denominator))) : 0;
      const score = Math.max(0, normalized[lag] +
        shift * (normalized[lag + 1] - normalized[lag - 1]) / 2 +
        shift * shift * (normalized[lag - 1] + normalized[lag + 1] - 2 * normalized[lag]) / 2);
      return { period: lag + shift, score };
    }
    let candidate = null;
    for (let lag = 2; lag <= maxLag; lag++){
      if (normalized[lag] >= 0.15) continue;
      while (lag < maxLag && normalized[lag + 1] < normalized[lag]) lag++;
      const refined = refine(lag);
      if (candidate === null){
        candidate = refined;
      } else {
        const ratio = refined.period / candidate.period;
        const multiple = Math.round(ratio);
        // A strong harmonic can pass YIN's first threshold before the fundamental.
        if (multiple >= 2 && multiple <= 4 && Math.abs(ratio - multiple) < 0.05 &&
            candidate.score - refined.score > 0.015 && refined.score < candidate.score * 0.5){
          candidate = refined;
        }
      }
    }
    if (candidate === null) return empty;
    const frequency = Math.round(rate / candidate.period * 10) / 10;
    if (frequency < MIN_HZ || frequency > MAX_HZ) return empty;
    return { frequency, clarity: Math.max(0, 1 - candidate.score), rms };
  }

  if (typeof module === 'object' && module.exports){
    module.exports = { detectFrequency };
    return;
  }

  const section = document.getElementById('belt-frequency');
  if (!section) return;
  const find = id => document.getElementById(id);
  const start = find('frequency-start');
  const stop = find('frequency-stop');
  const save = find('frequency-save');
  const belt = find('frequency-belt');
  const confirmed = find('frequency-stopped');
  const output = find('frequency-value');
  const status = find('frequency-status');
  const results = find('frequency-results');
  const savedStatus = find('frequency-saved-status');
  const key = `mconfig.belt-frequency:${window.location.origin}`;
  const belts = { stepper_x: 'X belt', stepper_y: 'Y belt', stepper_y1: 'Y1 belt' };
  const secure = new URL(window.location.href);
  secure.protocol = 'https:';
  secure.port = '8443';
  secure.hash = 'tuning';
  find('frequency-https-link').href = secure.href;
  find('frequency-https-hint').hidden = window.isSecureContext;

  let saved = {};
  let session = null;
  let generation = 0;
  let lastFrequency = null;
  let disposed = false;

  function message(text){
    status.textContent = text;
  }

  function renderSaved(){
    results.replaceChildren();
    for (const name of Object.keys(belts)){
      if (!saved[name]) continue;
      const item = document.createElement('div');
      item.setAttribute('role', 'group');
      item.setAttribute('aria-label', belts[name]);
      const label = document.createElement('div');
      label.className = 'k';
      label.textContent = belts[name];
      const value = document.createElement('div');
      value.className = 'v';
      value.textContent = `${saved[name].frequency.toFixed(1)} Hz`;
      const date = document.createElement('div');
      date.className = 'subhint';
      date.textContent = 'Saved: ' + new Date(saved[name].testedAt).toLocaleString();
      item.append(label, value, date);
      results.append(item);
    }
    savedStatus.textContent = Object.keys(saved).length ?
      'Saved on this phone/browser. Compare only the same belt and free span.' : 'No frequency readings saved yet.';
  }

  try {
    const serialized = localStorage.getItem(key);
    if (serialized !== null){
      const records = JSON.parse(serialized);
      if (!records || typeof records !== 'object' || Array.isArray(records)) throw new Error('Invalid saved readings.');
      for (const [name, value] of Object.entries(records)){
        if (!Object.prototype.hasOwnProperty.call(belts, name) || !value ||
            !Number.isFinite(value.frequency) || value.frequency < MIN_HZ || value.frequency > MAX_HZ ||
            typeof value.testedAt !== 'string' || !Number.isFinite(Date.parse(value.testedAt))){
          throw new Error('Invalid saved belt frequency.');
        }
      }
      saved = records;
    }
    renderSaved();
  } catch (error){
    savedStatus.textContent = 'Error loading saved frequency readings: ' + error.message;
  }

  function clearReading(){
    lastFrequency = null;
    output.textContent = '-- Hz';
    save.disabled = true;
    if (session){
      session.recent = [];
      session.captureUntil = 0;
    }
  }

  async function release(active){
    if (!active) return;
    if (active.timer !== null) window.clearInterval(active.timer);
    if (active.stream){
      for (const track of active.stream.getTracks()){
        track.removeEventListener('ended', active.ended);
        track.stop();
      }
    }
    if (active.source) active.source.disconnect();
    if (active.analyser) active.analyser.disconnect();
    if (active.context && active.context.state !== 'closed') await active.context.close();
  }

  async function stopListening(text){
    generation++;
    const active = session;
    session = null;
    start.disabled = false;
    stop.disabled = true;
    confirmed.disabled = false;
    if (active?.controls){
      active.controls.forEach(([control, disabled]) => { control.disabled = disabled; });
    }
    if (text && section.isConnected) message(text);
    try {
      await release(active);
    } catch (error){
      if (section.isConnected) message('Error releasing microphone: ' + error.message);
      else console.error('Error releasing belt-frequency microphone:', error);
    }
  }

  function microphoneError(error){
    if (error.name === 'NotAllowedError' || error.name === 'SecurityError'){
      return 'Microphone access was denied or blocked. Use trusted HTTPS and allow microphone access in this browser/site settings.';
    }
    if (error.name === 'NotFoundError') return 'No microphone was found on this device.';
    if (error.name === 'NotReadableError') return 'The microphone could not be opened. Close other apps using it and try again.';
    return error.message || String(error);
  }

  function sample(active){
    try {
      if (session !== active || !section.isConnected) return;
      if (active.context.state !== 'running') throw new Error('Audio capture was interrupted. Start the microphone again.');
      active.analyser.getFloatTimeDomainData(active.buffer);
      const reading = detectFrequency(active.buffer, active.context.sampleRate);
      const now = Date.now();
      if (now - active.started < 1000){
        active.background += reading.rms;
        active.backgroundCount++;
        return;
      }
      if (!active.calibrated){
        active.background = Math.max(0.001, active.background / Math.max(1, active.backgroundCount));
        active.calibrated = true;
        message('Listening. Pluck the middle of the free belt span; keep the phone close and the room quiet.');
      }
      if (now > active.captureUntil){
        if (reading.rms > Math.max(0.003, active.background * 2.5)){
          active.captureUntil = now + 2000;
          active.recent = [];
        } else {
          active.background = active.background * 0.95 + reading.rms * 0.05;
          return;
        }
      }
      if (reading.frequency === null || reading.rms < Math.max(0.002, active.background * 1.5)){
        active.recent = [];
        return;
      }
      active.recent.push(reading.frequency);
      if (active.recent.length > 3) active.recent.shift();
      if (active.recent.length < 3) return;
      const sorted = [...active.recent].sort((a, b) => a - b);
      if ((sorted[2] - sorted[0]) / sorted[1] > 0.02) return;
      lastFrequency = Math.round(sorted[1] * 10) / 10;
      output.textContent = `${lastFrequency.toFixed(1)} Hz`;
      save.disabled = false;
      message('Stable frequency detected. Pluck again to check consistency before saving; doubled readings can be harmonics.');
    } catch (error){
      void stopListening('Error measuring belt frequency: ' + error.message);
    }
  }

  start.addEventListener('click', async () => {
    if (session || disposed) return;
    if (!window.isSecureContext){
      message('Microphone access requires trusted HTTPS. Open the HTTPS configurator link, then allow microphone access.');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia){
      message('Microphone capture is unavailable in this browser. Use a current Safari or Chrome browser over trusted HTTPS.');
      return;
    }
    if (!confirmed.checked){
      message('Confirm that the printer is stopped before plucking a belt. This test never moves the printer.');
      return;
    }
    if (find('pid-go')?.disabled){
      message('A configurator printer action is running. Wait for it to finish before measuring a belt.');
      return;
    }
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context){
      message('Web Audio is unavailable in this browser.');
      return;
    }
    const token = ++generation;
    const active = { context: null, stream: null, source: null, analyser: null, timer: null,
      controls: [], recent: [], background: 0, backgroundCount: 0, calibrated: false, captureUntil: 0 };
    session = active;
    clearReading();
    start.disabled = true;
    stop.disabled = false;
    confirmed.disabled = true;
    active.controls = Array.from(document.querySelectorAll(
      '#pid-go, #bed-level-start, #bed-level-refresh, #bed-level-shimstock, .bed-level-point, #belt-run'
    )).map(control => [control, control.disabled]);
    active.controls.forEach(([control]) => { control.disabled = true; });
    message('Requesting microphone permission...');
    try {
      active.context = new Context();
      await active.context.resume();
      if (generation !== token || !section.isConnected) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: false, noiseSuppression: false, autoGainControl: false
      }, video: false });
      if (generation !== token || !section.isConnected){
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      active.stream = stream;
      active.ended = () => { void stopListening('Microphone capture ended. Start again to continue measuring.'); };
      stream.getTracks().forEach(track => track.addEventListener('ended', active.ended));
      active.source = active.context.createMediaStreamSource(stream);
      active.analyser = active.context.createAnalyser();
      active.analyser.fftSize = 32768;
      active.source.connect(active.analyser);
      active.buffer = new Float32Array(active.analyser.fftSize);
      active.started = Date.now();
      active.timer = window.setInterval(() => sample(active), 100);
      message('Calibrating background noise for one second. Keep quiet, then pluck the belt.');
    } catch (error){
      if (generation === token) await stopListening('Error starting microphone: ' + microphoneError(error));
    }
  });

  stop.addEventListener('click', () => stopListening('Microphone stopped. The last detected frequency remains available to save.'));
  belt.addEventListener('change', () => {
    clearReading();
    if (session) void stopListening('Belt changed. Start the microphone again before plucking the newly selected belt.');
  });
  save.addEventListener('click', () => {
    if (lastFrequency === null) return;
    if (!Object.prototype.hasOwnProperty.call(belts, belt.value)){
      savedStatus.textContent = 'Error: select a valid belt before saving.';
      return;
    }
    saved[belt.value] = { frequency: lastFrequency, testedAt: new Date().toISOString() };
    renderSaved();
    try {
      localStorage.setItem(key, JSON.stringify(saved));
    } catch (error){
      savedStatus.textContent = 'Error saving frequency reading: ' + error.message +
        '. This reading is visible only until you leave this tab.';
    }
  });

  const hidden = () => {
    if (document.visibilityState === 'hidden' && session) void stopListening('Microphone stopped because the page was hidden.');
  };
  const leavePage = () => { void stopListening('Microphone stopped because you left the page.'); };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    observer.disconnect();
    document.removeEventListener('visibilitychange', hidden);
    window.removeEventListener('pagehide', leavePage);
    void stopListening();
  };
  const observer = new MutationObserver(() => { if (!section.isConnected) dispose(); });
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('visibilitychange', hidden);
  window.addEventListener('pagehide', leavePage);
})();
