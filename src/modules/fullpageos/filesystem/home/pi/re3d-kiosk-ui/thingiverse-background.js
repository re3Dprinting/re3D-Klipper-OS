const designPage = 'https://www.thingiverse.com/re3dprinting/designs';
const designCache = 'http://localhost:8081/cgi-bin/thingiverse_designs.py';
const designAlarm = 're3d-thingiverse-designs';
let designCollection = null;
let collectingDesignTabId = null;

async function designRequest(url, options = {}) {
  const response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(10000), ...options});
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Design request failed (${response.status}).`);
  return data;
}

async function designPolicyAllowsCollection() {
  const policy = await designRequest('http://localhost:8081/auth/airgap');
  if (typeof policy.enabled !== 'boolean' || typeof policy.applied !== 'boolean'
      || policy.local !== true || !['off', 'basic', 'lockdown'].includes(policy.security_mode)) {
    throw new Error('Unable to verify the printer security policy for design collection.');
  }
  if (policy.enabled || policy.applied || policy.security_mode === 'lockdown') {
    return false;
  }
  return true;
}

async function updateDesigns(force) {
  if (!await designPolicyAllowsCollection()) {
    return {skipped: true, reason: 'Design checks are disabled in air-gap and Lockdown modes.'};
  }
  const cached = await designRequest(designCache);
  if (!force && cached.checkedAt && Date.now() - Date.parse(cached.checkedAt) < 6 * 60 * 60 * 1000) {
    return {skipped: true};
  }
  let tab;
  let pendingError = '';
  try {
    tab = await chrome.tabs.create({url: designPage, active: false});
    collectingDesignTabId = tab.id;
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      let result;
      try {
        result = await chrome.tabs.sendMessage(tab.id, {type: 're3d-read-designs'});
      } catch (error) {
        if (!/Receiving end does not exist|Could not establish connection/.test(error.message)) throw error;
      }
      if (result?.error && !result.pending) throw new Error(result.error);
      pendingError = result?.error || '';
      if (Array.isArray(result?.designs) && result.designs.length) {
        await designRequest(designCache, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({designs: result.designs})
        });
        return {ok: true};
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error(pendingError || 'Thingiverse did not display design cards within 45 seconds. Saved designs have been kept.');
  } catch (error) {
    try {
      await designRequest(designCache, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({error: error.message.slice(0, 300)})
      });
    } catch (cacheError) {
      throw new Error(error.message + ' Could not record the failed check: ' + cacheError.message);
    }
    throw error;
  } finally {
    collectingDesignTabId = null;
    if (tab) await chrome.tabs.remove(tab.id);
  }
}

function collectDesigns(force = false) {
  if (!designCollection) {
    designCollection = updateDesigns(force).finally(() => { designCollection = null; });
  }
  return designCollection;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type === 're3d-publish-designs') {
    (async () => {
      const url = new URL(sender.url);
      if (!sender.tab || sender.frameId !== 0 || url.protocol !== 'https:'
          || !['www.thingiverse.com', 'thingiverse.com'].includes(url.hostname)
          || !/^\/re3dprinting\/designs\/?$/.test(url.pathname)) {
        throw new Error('Only the public re3D Thingiverse page can publish displayed designs.');
      }
      if (sender.tab.id === collectingDesignTabId || !await designPolicyAllowsCollection()) return {skipped: true};
      await designRequest(designCache, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({designs: message.designs})
      });
      return {ok: true};
    })().then(respond, error => {
      console.error('Could not save displayed Thingiverse designs:', error);
      respond({error: error.message});
    });
    return true;
  }
  if (message?.type !== 're3d-refresh-designs') return;
  if (!sender.tab || !isLocalInterface(sender.url) || !isLocalInterface(sender.tab.url)) {
    respond({error: 'Only the local touchscreen can check Thingiverse designs.'});
    return;
  }
  collectDesigns(message.force === true).then(respond, error => {
    console.error('Thingiverse design collection failed:', error);
    respond({error: error.message});
  });
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === designAlarm) {
    collectDesigns().catch(error => console.error('Scheduled Thingiverse collection failed:', error));
  }
});
chrome.alarms.get(designAlarm).then(alarm => {
  if (!alarm) return chrome.alarms.create(designAlarm, {delayInMinutes: 1, periodInMinutes: 360});
}).catch(error => console.error('Could not schedule Thingiverse design collection:', error));
