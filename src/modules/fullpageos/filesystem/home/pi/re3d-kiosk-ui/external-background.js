const keyFor = id => `re3d-external-${id}`;

function isLocalInterface(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol)
      && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  } catch (error) {
    if (error instanceof TypeError) return false;
    throw error;
  }
}

async function contextFor(tab) {
  const key = keyFor(tab.id);
  const stored = await chrome.storage.session.get(key);
  if (stored[key]) return stored[key];
  if (tab.openerTabId === undefined) return null;
  const parentKey = keyFor(tab.openerTabId);
  const parent = (await chrome.storage.session.get(parentKey))[parentKey];
  if (!parent) return null;
  await chrome.storage.session.set({[key]: parent});
  return parent;
}

async function openExternal(url, sender) {
  if (!sender.tab || !isLocalInterface(sender.url) || !isLocalInterface(sender.tab.url)) {
    throw new Error('External browsing must start from the local printer interface.');
  }
  const destination = new URL(url);
  if (destination.protocol !== 'https:' || destination.username || destination.password
      || isLocalInterface(destination.href)) {
    throw new Error('This link is not an external HTTPS page.');
  }
  // Register before loading so redirects, early failures, and content scripts see the return route.
  const tab = await chrome.tabs.create({url: 'about:blank', active: false, windowId: sender.tab.windowId});
  try {
    await chrome.storage.session.set({
      [keyFor(tab.id)]: {returnTabId: sender.tab.id, returnUrl: sender.tab.url}
    });
    await chrome.tabs.update(tab.id, {url: destination.href, active: true});
  } catch (error) {
    await chrome.storage.session.remove(keyFor(tab.id));
    await chrome.tabs.remove(tab.id);
    throw error;
  }
}

async function returnToInterface(tab) {
  const context = await contextFor(tab);
  if (!context || !isLocalInterface(context.returnUrl)) {
    throw new Error('The return route for this page is unavailable.');
  }
  const tabs = await chrome.tabs.query({});
  let target = tabs.find(candidate => candidate.id === context.returnTabId && isLocalInterface(candidate.url));
  if (target) {
    await chrome.tabs.update(target.id, {active: true});
  } else {
    target = await chrome.tabs.create({url: context.returnUrl, active: true, windowId: tab.windowId});
  }
  await chrome.windows.update(target.windowId, {focused: true});
  await chrome.tabs.remove(tab.id);
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!['re3d-open-external', 're3d-external-context', 're3d-return-to-interface'].includes(message?.type)) return;
  (async () => {
    if (!sender.tab) throw new Error('This request has no browser tab.');
    if (message.type === 're3d-open-external') {
      await openExternal(message.url, sender);
      return {ok: true};
    }
    if (message.type === 're3d-external-context') return {managed: !!await contextFor(sender.tab)};
    await returnToInterface(sender.tab);
    return {ok: true};
  })().then(respond, error => {
    console.error('Touchscreen external navigation failed:', error);
    respond({error: error.message});
  });
  return true;
});

chrome.webNavigation.onErrorOccurred.addListener(details => {
  if (details.frameId !== 0 || details.error === 'net::ERR_ABORTED') return;
  (async () => {
    const tab = (await chrome.tabs.query({})).find(candidate => candidate.id === details.tabId);
    if (!tab || !await contextFor(tab)) return;
    await chrome.tabs.update(tab.id, {url: chrome.runtime.getURL('external-error.html')});
  })().catch(error => console.error('Could not show the external-page return screen:', error));
}, {url: [{schemes: ['http', 'https']}]});

chrome.webNavigation.onCreatedNavigationTarget.addListener(details => {
  (async () => {
    // Platform links often use noopener, so the child tab may not expose openerTabId.
    const key = keyFor(details.sourceTabId);
    const context = (await chrome.storage.session.get(key))[key];
    if (context) await chrome.storage.session.set({[keyFor(details.tabId)]: context});
  })().catch(error => console.error('Could not retain the return route for a new tab:', error));
});

chrome.tabs.onRemoved.addListener(id => {
  chrome.storage.session.remove(keyFor(id))
    .catch(error => console.error('Could not clear the external-page return route:', error));
});
