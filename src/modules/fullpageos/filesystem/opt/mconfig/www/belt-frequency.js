(() => {
  const MIN_HZ = 20;
  const MAX_HZ = 400;
  const MIN_SIGNAL_RMS = 0.00025;

  function detectFrequency(input, sampleRate){
    if (!input || !Number.isInteger(input.length) || input.length < 2 ||
        !Number.isFinite(sampleRate) || sampleRate <= 0){
      throw new Error('Invalid microphone sample buffer or sample rate.');
    }
    const stride = Math.max(1, Math.floor(sampleRate / 8000));
    const rate = sampleRate / stride;
    const count = Math.min(4096, Math.floor(input.length / stride));
    let maxLag = Math.floor(rate / MIN_HZ);
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
    let rms = Math.sqrt(energy / count);
    if (!Number.isFinite(rms)) throw new Error('Microphone returned invalid audio samples.');
    if (rms < MIN_SIGNAL_RMS) return { frequency: null, clarity: 0, rms };

    const blockSize = Math.max(1, Math.round(rate * 0.01));
    const blocks = [];
    for (let start = 0; start < count; start += blockSize){
      const end = Math.min(count, start + blockSize);
      let sum = 0;
      for (let index = start; index < end; index++) sum += samples[index] * samples[index];
      blocks.push(sum / (end - start));
    }
    const peak = Math.max(...blocks);
    const first = blocks.findIndex(value => value >= peak * 0.1);
    // Skip the partly filled onset block: leading silence is not part of the ringing belt.
    const begin = first === 0 ? 0 : Math.min(count, (first + 1) * blockSize);
    const region = samples.subarray(begin, Math.min(count, begin + Math.round(rate * 0.25)));
    const sums = new Float64Array(region.length + 1);
    const energies = new Float64Array(region.length + 1);
    for (let index = 0; index < region.length; index++){
      sums[index + 1] = sums[index] + region[index];
      energies[index + 1] = energies[index] + region[index] * region[index];
    }
    rms = region.length ? Math.sqrt(energies[region.length] / region.length) : 0;
    const empty = { frequency: null, clarity: 0, rms };
    maxLag = Math.min(maxLag, Math.floor(region.length / 2) - 1);
    if (rms < MIN_SIGNAL_RMS || maxLag < 2) return empty;

    const difference = new Float64Array(maxLag + 2);
    const normalized = new Float64Array(maxLag + 2);
    let total = 0;
    const windowSize = region.length - maxLag - 1;
    const leftSum = sums[windowSize];
    const leftEnergy = Math.max(0, energies[windowSize] - leftSum * leftSum / windowSize);
    for (let lag = 1; lag <= maxLag + 1; lag++){
      let product = 0;
      for (let index = 0; index < windowSize; index++){
        product += region[index] * region[index + lag];
      }
      const rightSum = sums[windowSize + lag] - sums[lag];
      const rightEnergy = Math.max(0, energies[windowSize + lag] - energies[lag] - rightSum * rightSum / windowSize);
      const scale = Math.sqrt(leftEnergy * rightEnergy);
      // Normalize amplitude independently so a fast decay does not look like a pitch mismatch.
      const correlation = scale > 0 ? (product - leftSum * rightSum / windowSize) / scale : 0;
      const distance = 1 - Math.max(-1, Math.min(1, correlation));
      difference[lag] = distance;
      total += distance;
      normalized[lag] = total > 0 ? distance * lag / total : 1;
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
  const inputMeter = find('frequency-input');
  const inputValue = find('frequency-input-value');
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
    if (status.textContent !== text) status.textContent = text;
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
    if (active.silentOutput) active.silentOutput.disconnect();
    if (active.context && active.context.state !== 'closed') await active.context.close();
  }

  async function stopListening(text){
    generation++;
    const active = session;
    session = null;
    start.disabled = false;
    stop.disabled = true;
    confirmed.disabled = false;
    inputMeter.value = -90;
    inputValue.textContent = 'Stopped';
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
      const now = Date.now();
      let energy = 0;
      const meterSize = Math.min(active.buffer.length, Math.round(active.context.sampleRate * 0.05));
      for (let index = active.buffer.length - meterSize; index < active.buffer.length; index++){
        energy += active.buffer[index] * active.buffer[index];
      }
      const level = Math.sqrt(energy / meterSize);
      const decibels = level > 0 ? Math.max(-90, Math.min(0, 20 * Math.log10(level))) : -90;
      inputMeter.value = decibels;
      inputValue.textContent = `${Math.round(decibels)} dBFS`;
      if (level > 0.000001) active.lastAudio = now;
      const reading = detectFrequency(active.buffer, active.context.sampleRate);
      if (now - active.started < 1000){
        active.background += reading.rms;
        active.backgroundCount++;
        return;
      }
      if (!active.calibrated){
        active.background = Math.max(MIN_SIGNAL_RMS / 4, active.background / Math.max(1, active.backgroundCount));
        active.calibrated = true;
        message('Listening. Pluck the middle of the free belt span; keep the phone close and the room quiet.');
      }
      if (now > active.captureUntil){
        if (reading.rms > Math.max(MIN_SIGNAL_RMS * 2, active.background * 2.5)){
          active.captureUntil = now + 2000;
          active.recent = [];
          message('Sound detected. Checking the belt frequency...');
        } else {
          active.background = active.background * 0.95 + reading.rms * 0.05;
          if (now - active.lastAudio > 2000){
            message('Listening, but no microphone signal is arriving. Tap or speak near the phone and check the input meter; check the selected microphone or Bluetooth connection if it stays at -90 dBFS.');
          }
          return;
        }
      }
      if (reading.frequency === null || reading.rms < Math.max(MIN_SIGNAL_RMS, active.background * 1.5)){
        active.recent = [];
        message('Sound detected, but no clear belt frequency yet. Move the microphone closer, reduce background noise and pluck again.');
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
    const active = { context: null, stream: null, source: null, analyser: null, silentOutput: null, timer: null,
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
      await active.context.resume();
      if (generation !== token || !section.isConnected) return;
      active.ended = () => { void stopListening('Microphone capture ended. Start again to continue measuring.'); };
      stream.getTracks().forEach(track => track.addEventListener('ended', active.ended));
      active.source = active.context.createMediaStreamSource(stream);
      active.analyser = active.context.createAnalyser();
      active.analyser.fftSize = 32768;
      active.source.connect(active.analyser);
      // Keep Safari's processing graph connected without playing microphone audio.
      active.silentOutput = active.context.createGain();
      active.silentOutput.gain.value = 0;
      active.analyser.connect(active.silentOutput);
      active.silentOutput.connect(active.context.destination);
      active.buffer = new Float32Array(active.analyser.fftSize);
      active.started = Date.now();
      active.lastAudio = active.started;
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
