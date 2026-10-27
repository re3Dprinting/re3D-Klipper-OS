(() => {
  if (new URLSearchParams(location.search).get('setup') !== '1') return;
  window.re3dSetup = true;
  document.body.classList.add('guided-setup');
  document.title = 'Guided Setup - re:3D';

  const steps = ['welcome', 'machine', 'network', 'display', 'finish'];
  const titles = ['Welcome to your Gigabot', 'Choose your machine', 'Connect your printer', 'Choose your display', 'Ready to finish'];
  const descriptions = [
    'Your next big idea starts here. Let us get your Gigabot ready to print.',
    'Select the correct model and hardware options, then press Set Machine before continuing. Changes take effect after the final reboot.',
    'Connect to Wi-Fi or use Ethernet. You can continue offline. Changing networks may disconnect a remote browser; reopen the Configurator at the new address to resume.',
    'Choose the interface for the printer touchscreen. It will switch after setup is finished.',
    'Finish saves setup as complete and reboots into your selected interface. Calibrate the printer before your first print.'
  ];
  const byId = id => document.getElementById(id);
  const next = byId('setup-next');
  const back = byId('setup-back');
  const skip = byId('setup-skip');
  const status = byId('setup-status');
  const page = byId('page');
  let step = 'welcome';
  let busy = false;
  let machineSaved = false;
  let initialized = false;

  for (const id of ['setup-header', 'setup-status', 'setup-actions']) byId(id).hidden = false;
  byId('setup-progress').innerHTML = steps.map((name, index) =>
    `<li data-step="${name}">${index + 1}. ${['Welcome', 'Machine', 'Network', 'Display', 'Finish'][index]}</li>`).join('');

  function controls() {
    next.disabled = busy || !initialized || (step === 'machine' && !machineSaved);
    back.disabled = busy || !initialized;
    back.hidden = step === 'welcome';
    skip.disabled = busy || !initialized;
    next.textContent = step === 'finish' ? 'Finish & reboot' : 'Continue';
  }

  async function request(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    const data = await response.json();
    if (!response.ok || data.error || data.status === 'error') {
      throw new Error(data.error || data.message || `Request failed (${response.status})`);
    }
    return data;
  }

  async function saveStep(value) {
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
    byId('setup-progress').querySelectorAll('li').forEach(item => {
      if (item.dataset.step === step) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });
    if (step === 'machine' || step === 'network') {
      if (step === 'machine') machineSaved = false;
      if (!await loadTab(step)) throw new Error('Unable to load this setup screen. Reload to retry.');
    } else {
      cleanupTabArtifacts();
      currentTab = null;
      page.innerHTML = '';
      if (step === 'welcome') {
        page.innerHTML = `<div class="card">
          <div class="card-title">Make it yours</div>
          <p>We will walk you through your machine and hardware options, connect your Gigabot to your network, and help you choose the touchscreen experience that suits you.</p>
          <p>Progress is saved on the printer. If you skip for now, setup will resume the next time it starts.</p>
          <p>Keep the printer idle during setup. These steps do not move or heat the printer.</p>
        </div>`;
      } else if (step === 'display') {
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
      } else {
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
    if (step === 'finish') {
      await saveStep('completed');
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
    const target = steps[steps.indexOf(step) + 1];
    await saveStep(target);
    step = target;
    await render();
  }));

  back.addEventListener('click', () => act(async () => {
    const target = steps[steps.indexOf(step) - 1];
    await saveStep(target);
    step = target;
    await render();
  }));

  skip.addEventListener('click', () => {
    // Deliberately leave persisted progress intact for the next boot.
    location.href = `http://${location.hostname}:80/`;
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
    if (state.step === null || state.step === 'completed') {
      await saveStep('welcome');
    } else if (steps.includes(state.step)) {
      step = state.step;
    } else {
      throw new Error('Invalid saved setup progress.');
    }
    await render();
    initialized = true;
  });
})();
