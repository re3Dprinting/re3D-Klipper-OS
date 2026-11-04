(() => {
  const params = new URLSearchParams(location.search);
  if (params.get('setup') !== '1') return;
  // Retired preview links must never start a live setup session.
  if (params.has('preview')) {
    location.replace('/#system');
    return;
  }
  const openBox = params.get('flow') === 'openbox';
  window.re3dSetup = true;
  document.body.classList.add('guided-setup');
  document.title = openBox ? 'Open-box Setup - re:3D' : 'Guided Setup - re:3D';

  const steps = openBox ? ['welcome', 'precautions', 'network', 'finish'] : ['machine', 'network', 'display', 'finish'];
  const titles = openBox
    ? ['Welcome to your printer', 'A few things before you start', 'Choose your network mode', 'Ready to finish']
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
  const setupHeader = byId('setup-header');
  const actions = byId('setup-actions');
  const setupContainer = page.parentElement;
  const networkChoices = byId('setup-network-choice');
  const networkInputs = [byId('setup-network-connect'), byId('setup-network-offline')];
  byId('setup-airgap-settings').href = '/auth/airgap-settings?setup=' + (openBox ? 'openbox' : 'machine');
  let step = steps[0];
  let networkMode = null;
  let busy = false;
  let machineSaved = false;
  let initialized = false;
  let welcomeMachine = null;
  let airgapDisplay = false;

  for (const id of ['setup-header', 'setup-status', 'setup-actions']) byId(id).hidden = false;
  if (openBox) byId('setup-eyebrow').textContent = 'OPEN-BOX SETUP';
  const labels = openBox ? ['Welcome', 'Precautions', 'Network', 'Finish'] : ['Machine', 'Network', 'Display', 'Finish'];
  byId('setup-progress').innerHTML = steps.map((name, index) =>
    `<li data-step="${name}">${index + 1}. ${labels[index]}</li>`).join('');

  function controls() {
    next.disabled = busy || !initialized || (step === 'machine' && !machineSaved)
      || (step === 'network' && !networkMode);
    back.disabled = busy || !initialized;
    back.hidden = step === steps[0];
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
    document.body.classList.remove('openbox-welcome-screen');
    setupContainer.insertBefore(setupHeader, networkChoices);
    setupContainer.append(status, actions);
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
      if (step === 'welcome') {
        if (!welcomeMachine) {
          const machines = new Map([
            ['Gigabot 4', {image: 'gigabot-4', width: 768, height: 768}],
            ['Gigabot 4 XLT', {image: 'gigabot-4-xlt', width: 768, height: 768}],
            ['Terabot 4', {image: 'terabot-4', width: 768, height: 768}],
            ['GigabotX 2', {image: 'gigabotx-2', width: 768, height: 768}],
            ['GigabotX XLT 2', {image: 'gigabotx-2-xlt', width: 768, height: 768}],
            ['TerabotX 2', {image: 'terabotx-2', width: 768, height: 768}]
          ]);
          const response = await fetch('/cgi-bin/get_machine_type.sh', {cache: 'no-store'});
          if (!response.ok) throw new Error(`Unable to load the configured machine (${response.status}). Reload to retry.`);
          const name = (await response.text()).trim();
          const artwork = machines.get(name);
          if (!artwork) throw new Error('Unsupported configured machine. Check Machine settings in Configurator and reload.');
          welcomeMachine = {name, ...artwork};
        }
        byId('setup-title').textContent = `Welcome to your ${welcomeMachine.name}`;
        page.innerHTML = `<div class="card openbox-welcome">
          <div class="openbox-welcome-copy">
            <span class="openbox-kicker">MADE FOR BIG IDEAS</span>
            <h2 id="openbox-welcome-title" tabindex="-1">Dream BIG.<br><span>Print HUGE.</span></h2>
            <p>Your ${welcomeMachine.name} is configured. Just a few precautions and a network choice, and you are on your way.</p>
            <div class="openbox-welcome-route"><span>01 &nbsp; Check</span><span>02 &nbsp; Connect</span><span>03 &nbsp; Create</span></div>
            <p class="hint">No machine configuration needed. Let's get you started.</p>
          </div>
          <img class="openbox-welcome-image" src="/img/openbox-${welcomeMachine.image}.png" width="${welcomeMachine.width}" height="${welcomeMachine.height}"
            alt="${welcomeMachine.name} printer">
        </div>`;
        const welcomeCard = page.querySelector('.openbox-welcome');
        welcomeCard.prepend(setupHeader);
        welcomeCard.append(status, actions);
        document.body.classList.add('openbox-welcome-screen');
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
        const isolation = await request('/auth/airgap');
        if (typeof isolation.enabled !== 'boolean' || typeof isolation.applied !== 'boolean') {
          throw new Error('Unable to verify air-gap mode before selecting the display.');
        }
        airgapDisplay = isolation.enabled || isolation.applied;
        page.querySelector(`input[value="${airgapDisplay ? 'mainsail' : mode}"]`).checked = true;
        if (airgapDisplay) {
          page.querySelectorAll('input[name="setup-display"]').forEach(input => { input.disabled = true; });
          const note = document.createElement('p');
          note.textContent = 'Air-gapped mode uses PIN-protected Mainsail. Your saved display preference is retained until you leave air-gapped mode.';
          page.append(note);
        }
      } else if (step === 'finish') {
        page.innerHTML = openBox ? `<div class="card">
          <div class="card-title">Finish open-box setup</div>
          <p>Finish &amp; reboot closes this guide and opens the printer interface already configured by operations.</p>
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
          <img class="openbox-guide-qr" src="/img/getting-started-qr.png" width="147" height="147"
            alt="QR code for Gigabot Getting Started Guides">
          <div><div class="card-title">The full guide, on your phone</div>
            <p>Scan to find the Getting Started Guide for your printer. Read the full guide before operating your printer.</p>
            <p><a href="https://re3d.zendesk.com/hc/en-us/articles/360038324771-Gigabot-Getting-Started-Guides" target="_blank" rel="noopener noreferrer">Open Getting Started Guides</a></p>
          </div>
        </aside>`;
      }
    }
    controls();
    byId(step === 'welcome' ? 'openbox-welcome-title' : 'setup-title').focus();
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
    if (step === 'network') {
      const isolation = await request('/auth/airgap');
      if (typeof isolation.enabled !== 'boolean' || typeof isolation.applied !== 'boolean') {
        throw new Error('Unable to verify air-gap mode. Reload setup before continuing.');
      }
      if (networkMode === 'offline' && !isolation.enabled) {
        throw new Error('Open Air-gap settings, enable isolation with an administrator password, then return to setup.');
      }
      if (networkMode === 'connect' && (isolation.enabled || isolation.applied)) {
        throw new Error('Air-gap mode is enabled or still active. Use System > Air-gap settings to disable it and reboot before connecting.');
      }
    }
    if (step === 'finish') {
      await setSetupState('completed');
      const response = await fetch('/cgi-bin/reboot.sh', {method: 'POST'});
      if (!response.ok) throw new Error(`Setup saved, but reboot failed (${response.status}). Retry Finish or reboot manually.`);
      status.textContent = 'Setup saved. Rebooting into your selected interface...';
      initialized = false;
      return;
    }
    if (step === 'display' && !airgapDisplay) {
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
    if (!openBox && state.step === 'openbox') {
      throw new Error('Open-box setup is scheduled. Open /?setup=1&flow=openbox to run the customer guide.');
    }
    await setSetupState(openBox ? 'openbox' : 'machine');
    await render();
    initialized = true;
  });
})();
