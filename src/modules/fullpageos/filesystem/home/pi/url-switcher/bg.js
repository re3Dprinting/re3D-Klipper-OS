chrome.runtime.onMessage.addListener(async (msg, _sender, _sendResponse) => {
  if (msg.type !== "TOGGLE_TABS") return;
  const { urlA, urlB } = msg.cfg;

  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!active) return;

  // Ensure A/B exist
  const tabs = await chrome.tabs.query({ currentWindow: true });
  let tabA = tabs.find(t => (t.url || "").startsWith(urlA));
  let tabB = tabs.find(t => (t.url || "").startsWith(urlB));

  if (!tabA) tabA = await chrome.tabs.create({ url: urlA, active: false });
  if (!tabB) tabB = await chrome.tabs.create({ url: urlB, active: false });

  const target = (active.id === (tabA.id || tabA)) ? tabB : tabA;
  chrome.tabs.update(target.id || target, { active: true });
});