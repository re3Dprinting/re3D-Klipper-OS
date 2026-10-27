(() => {
  if (new URLSearchParams(location.search).get('setup') !== '1') return;
  window.re3dSetup = true;
  document.body.classList.add('guided-setup');
  document.title = 'Guided Setup - re:3D';

  const steps = ['machine', 'network', 'display', 'finish'];
  const titles = ['Choose your machine', 'Choose your network mode', 'Choose your display', 'Ready to finish'];
  const descriptions = [
    'Select the correct model and hardware options, then press Set Machine before continuing. Changes take effect after the final reboot. If you leave or reload setup, you will start again at Machine.',
    'Connect your printer to a network, or continue with offline setup.',
    'Choose the interface for the printer touchscreen. It will switch after setup is finished.',
    'Finish saves setup as complete and reboots into your selected interface. Calibrate the printer before your first print.'
  ];
  const byId = id => document.getElementById(id);
  const next = byId('setup-next');
  const back = byId('setup-back');
  const skip = byId('setup-skip');
  const status = byId('setup-status');
  const page = byId('page');
  const networkChoices = byId('setup-network-choice');
  const networkInputs = [byId('setup-network-connect'), byId('setup-network-offline')];
  let step = 'machine';
  let networkMode = null;
  let busy = false;
  let machineSaved = false;
  let initialized = false;

  for (const id of ['setup-header', 'setup-status', 'setup-actions']) byId(id).hidden = false;
  byId('setup-progress').innerHTML = steps.map((name, index) =>
    `<li data-step="${name}">${index + 1}. ${['Machine', 'Network', 'Display', 'Finish'][index]}</li>`).join('');

  function controls() {
    next.disabled = busy || !initialized || (step === 'machine' && !machineSaved)
      || (step === 'network' && !networkMode);
    back.disabled = busy || !initialized;
    back.hidden = step === 'machine';
    skip.disabled = busy || !initialized;
    next.textContent = step === 'finish' ? 'Finish & reboot' : 'Continue';
    networkInputs.forEach(input => { input.disabled = busy || !initialized; });
  }

  async function request(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok || data.error || data.status === 'error') {
      throw new Error(data.error || data.message || `Request failed (${response.status})`);
    }
    return data;
  }

  async function setSetupState(value) {
    await request('/cgi-bin/setup.py', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({step: value})
    });
  }

  async function render() {
    const index = steps.indexOf(step);
    byId('setup-title').textContent = titles[index];
    byId('setup-description').textContent = descriptions[index];
    networkChoices.hidden = step !== 'network';
    byId('setup-network-offline-note').hidden = step !== 'network' || networkMode !== 'offline';
    byId('setup-progress').querySelectorAll('li').forEach(item => {
      if (item.dataset.step === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    if (step === 'machine' || (step === 'network' && networkMode === 'connect')) {
      if (step === 'machine') machineSaved = false;
      if (!await loadTab(step)) throw new Error('Unable to load this setup screen. Reload to retry.');
    } else {
      cleanupTabArtifacts();
      currentTab = null;
      page.innerHTML = '';
      if (step === 'display') {
        page.innerHTML = `<div class="card">
          <div class="card-title">Touchscreen interface</div>
          <div class="setup-choices" role="radiogroup" aria-label="Touchscreen interface">
            <label>
              <img class="setup-preview" src="/img/setup-mainsail.png" width="1154" height="768"
                alt="Mainsail dashboard showing printer status, temperatures, toolhead controls, preheat presets, and control macros">
              <div class="setup-choice-heading">
                <input type="radio" name="setup-display" value="mainsail"
                  aria-labelledby="setup-mainsail-title" aria-describedby="setup-mainsail-description">
                <strong id="setup-mainsail-title">Mainsail (default)</strong>
              </div>
              <div id="setup-mainsail-description">
                <span class="setup-choice-tagline">Information-rich &amp; maximalist</span>
                <p>More information at a glance. A full dashboard with detailed graphs, printer status, and controls for those who like to see it all.</p>
              </div>
            </label>
            <label>
              <img class="setup-preview" src="/img/setup-klipperscreen.png" width="480" height="320"
                alt="KlipperScreen home screen showing temperatures and large touch-friendly printer controls">
              <div class="setup-choice-heading">
                <input type="radio" name="setup-display" value="klipperscreen"
                  aria-labelledby="setup-klipperscreen-title" aria-describedby="setup-klipperscreen-description">
                <strong id="setup-klipperscreen-title">KlipperScreen</strong>
              </div>
              <div id="setup-klipperscreen-description">
                <span class="setup-choice-tagline">Minimalist &amp; snappy</span>
                <p>A simpler, snappier touchscreen experience. Fewer distractions, large touch-friendly controls, and quick access to everyday printing tasks.</p>
              </div>
            </label>
          </div>
          <p>You can change this later in Configurator &gt; System.</p>
        </div>`;
        const response = await fetch('/cgi-bin/get_display_mode.sh', {cache: 'no-store'});
        if (!response.ok) throw new Error(`Unable to load display mode (${response.status}).`);
        const mode = (await response.text()).trim();
        if (!['mainsail', 'klipperscreen'].includes(mode)) throw new Error('Invalid saved display mode.');
        page.querySelector(`input[value="${mode}"]`).checked = true;
      } else if (step === 'finish') {
        page.innerHTML = `<div class="card">
          <div class="card-title">Setup complete?</div>
          <p>Use Back to review settings, or Finish &amp; reboot to apply your machine and display choices.</p>
          <p>After reboot, use Configurator &gt; Tuning for temperature control and bed leveling, then Calibration for material profiles.</p>
          <p>You can reopen this wizard from Configurator &gt; System at any time.</p>
        </div>`;
      }
    }
    controls();
    byId('setup-title').focus();
    window.scrollTo({top: 0, behavior: 'auto'});
  }

  async function act(action) {
    if (busy) return;
    busy = true;
    status.textContent = '';
    controls();
    try {
      await action();
    } catch (error) {
      status.textContent = error.message || String(error);
    } finally {
      busy = false;
      controls();
    }
  }

  next.addEventListener('click', () => act(async () => {
    if (step === 'network' && !networkMode) {
      throw new Error('Choose a network mode before continuing.');
    }
    if (step === 'finish') {
      await setSetupState('completed');
      const response = await fetch('/cgi-bin/reboot.sh', {method: 'POST'});
      if (!response.ok) throw new Error(`Setup saved, but reboot failed (${response.status}). Retry Finish or reboot manually.`);
      status.textContent = 'Setup saved. Rebooting into your selected interface...';
      initialized = false;
      return;
    }
    if (step === 'display') {
      const selected = page.querySelector('input[name="setup-display"]:checked');
      if (!selected) throw new Error('Choose a display interface before continuing.');
      await request('/cgi-bin/set_display_mode.sh', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({mode: selected.value, reboot: 'defer'})
      });
    }
    step = steps[steps.indexOf(step) + 1];
    await render();
  }));

  back.addEventListener('click', () => act(async () => {
    step = steps[steps.indexOf(step) - 1];
    await render();
  }));

  skip.addEventListener('click', () => {
    location.href = `http://${location.hostname}:80/`;
  });

  networkInputs.forEach(input => {
    input.addEventListener('change', async () => {
      await act(async () => {
        networkMode = input.value;
        await render();
      });
      input.focus();
    });
  });

  page.addEventListener('change', () => {
    if (step === 'machine') {
      machineSaved = false;
      controls();
    }
  });
  page.addEventListener('submit', event => {
    if (step === 'machine' && event.target.id === 'form-machine') {
      busy = true;
      machineSaved = false;
      controls();
    }
  }, true);
  window.addEventListener('re3d-machine-result', event => {
    if (step !== 'machine') return;
    busy = false;
    machineSaved = event.detail.ok;
    status.textContent = event.detail.ok
      ? 'Machine settings saved. Continue setup; the printer will reboot at the end.'
      : 'Machine settings were not saved. Check the error above and try again.';
    controls();
  });

  controls();
  act(async () => {
    const state = await request('/cgi-bin/setup.py');
    if (state.flashing) throw new Error('Finish firmware flashing and power-cycle the printer before setup.');
    await setSetupState('machine');
    await render();
    initialized = true;
  });
})();
