(() => {
  if (new URLSearchParams(location.search).get('setup') !== '1') return;
  const openBox = new URLSearchParams(location.search).get('flow') === 'openbox';
  const preview = openBox && new URLSearchParams(location.search).get('preview') === '1';
  window.re3dSetup = true;
  document.body.classList.add('guided-setup');
  document.title = openBox ? 'Open-box Setup - re:3D' : 'Guided Setup - re:3D';

  const steps = openBox ? ['welcome', 'precautions', 'network', 'finish'] : ['machine', 'network', 'display', 'finish'];
  const titles = openBox
    ? ['Welcome to your Gigabot', 'A few things before you start', 'Choose your network mode', 'Ready to finish']
    : ['Choose your machine', 'Choose your network mode', 'Choose your display', 'Ready to finish'];
  const descriptions = openBox ? [
    'Big ideas start here.',
    'Check these precautions before moving, homing, or printing.',
    'Connect your printer to a network, or continue with offline setup.',
    'Finish closes open-box setup and reboots into the interface already selected by operations.'
  ] : [
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
  let step = steps[0];
  let networkMode = null;
  let busy = false;
  let machineSaved = false;
  let initialized = false;

  for (const id of ['setup-header', 'setup-status', 'setup-actions']) byId(id).hidden = false;
  if (openBox) byId('setup-eyebrow').textContent = 'OPEN-BOX SETUP';
  byId('setup-preview-notice').hidden = !preview;
  const labels = openBox ? ['Welcome', 'Precautions', 'Network', 'Finish'] : ['Machine', 'Network', 'Display', 'Finish'];
  byId('setup-progress').innerHTML = steps.map((name, index) =>
    `<li data-step="${name}">${index + 1}. ${labels[index]}</li>`).join('');

  function controls() {
    next.disabled = busy || !initialized || (step === 'machine' && !machineSaved)
      || (step === 'network' && !networkMode);
    back.disabled = busy || !initialized;
    back.hidden = step === steps[0];
    skip.disabled = busy || !initialized;
    next.textContent = step === 'finish' ? (preview ? 'Finish preview' : 'Finish & reboot') : 'Continue';
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
    if (preview && step === 'finish') {
      byId('setup-description').textContent = 'Finish returns to System without changing setup status or rebooting.';
    }
    page.inert = preview && step === 'network';
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
      if (step === 'welcome') {
        page.innerHTML = `<div class="card openbox-welcome">
          <div class="openbox-welcome-copy">
            <span class="openbox-kicker">MADE FOR BIG IDEAS</span>
            <h2>Think big.<br><span>Print huge.</span></h2>
            <p>Your Gigabot is configured. Just a few precautions and a network choice, and you are on your way.</p>
            <div class="openbox-welcome-route"><span>01 &nbsp; Check</span><span>02 &nbsp; Connect</span><span>03 &nbsp; Create</span></div>
            <p class="hint">No machine configuration needed. Let's get you started.</p>
          </div>
          <svg class="openbox-printer" viewBox="0 0 320 320" aria-hidden="true" focusable="false">
            <g fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round">
              <path class="openbox-frame" pathLength="1" d="M65 70 115 38 265 38 265 248 215 282 65 282Z M65 70H215V282 M215 70 265 38 M115 38V70 M65 250H215L265 218"/>
              <path class="openbox-rail" d="M78 104H203 M90 78V245 M191 78V245"/>
              <path class="openbox-bed" d="M86 227H195L231 207H121Z"/>
            </g>
            <g class="openbox-print" fill="none" stroke="var(--brand)" stroke-width="5" stroke-linecap="round">
              <path d="M125 212H173 M125 201H173 M125 190H173 M125 179H173 M125 168H173"/>
            </g>
            <g class="openbox-nozzle" fill="var(--brand)"><path d="M137 94H161V115L149 127 137 115Z"/></g>
            <circle class="openbox-ready" cx="265" cy="275" r="20" fill="var(--brand)"/>
            <path class="openbox-ready" d="m256 275 6 6 12-13" fill="none" stroke="var(--on-action)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>`;
      } else if (step === 'precautions') {
        page.innerHTML = `<div class="card">
          <div class="card-title">Before your first print</div>
          <div class="openbox-slide">
            <figure><img src="/img/openbox-bed-locks.jpg" width="698" height="698" alt="Removing a shipping bed lock from the Gigabot bed frame with a wrench"><figcaption>Shipping restraint removal: guide page 11, A6.</figcaption></figure>
            <div>
              <p class="openbox-warning"><strong>Remove all eight shipping restraints before any movement.</strong> Check above AND below the bed frame. Bed locks: 8 mm wrench. Triangle braces: 4 mm hex driver. Save them for future transport.</p>
              <ul class="openbox-precautions">
                <li><strong>Unpack completely.</strong> Remove shipping packaging and ties, not permanent wiring. If lifting is needed, at least two people must hold the outer frame.</li>
                <li><strong>Check your space and power.</strong> Use a stable location, clear ventilation, and the grounded supply specified on the printer label. Never bypass the ground pin.</li>
                <li><strong>Check before printing.</strong> Follow the full guide for bed leveling, Z-home, filament loading, and print profiles. This wizard does not perform those procedures.</li>
              </ul>
              <p class="hint">Damage or tripped shipping indicators? Report to <a href="mailto:shipping@re3d.org">shipping@re3d.org</a> within 24 hours. Unsure about a restraint? Stop and contact <a href="mailto:support@re3d.org">support@re3d.org</a>.</p>
            </div>
          </div>
          <p class="hint">Gigabot 4 Getting Started Guide, revision 04, key points from pages 3-4 and 9-30. Read the full guide and follow the instructions for your model.</p>
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
      } else if (step === 'finish') {
        page.innerHTML = openBox ? `<div class="card">
          <div class="card-title">Finish open-box setup</div>
          <p>${preview ? 'Finish preview returns to System without rebooting or changing setup status.' : 'Finish &amp; reboot closes this guide and opens the printer interface already configured by operations.'}</p>
          <p>Confirm all shipping restraints and packaging have been removed. Read the full guide and complete the required pre-print checks before operating the printer.</p>
          <p>Your machine and display settings have not been changed by this guide.</p>
        </div>` : `<div class="card">
          <div class="card-title">Setup complete?</div>
          <p>Use Back to review settings, or Finish &amp; reboot to apply your machine and display choices.</p>
          <p>After reboot, use Configurator &gt; Tuning for temperature control and bed leveling, then Calibration for material profiles.</p>
          <p>You can reopen this wizard from Configurator &gt; System at any time.</p>
        </div>`;
      }
      if (openBox && step === 'precautions') {
        page.innerHTML += `<aside class="card openbox-guide" aria-label="Full getting started guide">
          <div class="openbox-qr-placeholder" role="img" aria-label="QR code placeholder, not scannable">QR code<br>coming soon</div>
          <div><div class="card-title">The full guide, on your phone</div>
            <p>Online guide link coming soon. This QR placeholder is not scannable; use your supplied Getting Started Guide for now.</p>
          </div>
        </aside>`;
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
      if (preview) {
        location.href = '/#system';
        return;
      }
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
    location.href = preview ? '/#system' : `http://${location.hostname}:80/`;
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
    if (preview) {
      await render();
      initialized = true;
      return;
    }
    const state = await request('/cgi-bin/setup.py');
    if (state.flashing) throw new Error('Finish firmware flashing and power-cycle the printer before setup.');
    if (!openBox && state.step === 'openbox') {
      throw new Error('Open-box setup is scheduled. Open /?setup=1&flow=openbox to run the customer guide.');
    }
    await setSetupState(openBox ? 'openbox' : 'machine');
    await render();
    initialized = true;
  });
})();
