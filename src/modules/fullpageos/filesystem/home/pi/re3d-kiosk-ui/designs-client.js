(() => {
  document.documentElement.setAttribute('data-re3d-design-collector', '');
  document.addEventListener('re3d-designs-refresh', async event => {
    const root = event.target;
    if (root.id !== 'news-page') return;
    let error = null;
    try {
      const result = await chrome.runtime.sendMessage({
        type: 're3d-refresh-designs', force: event.detail?.force === true
      });
      if (!result || result.error) throw new Error(result?.error || 'The design collector did not respond.');
    } catch (failure) {
      error = failure.message;
    }
    if (root.isConnected) root.dispatchEvent(new CustomEvent('re3d-designs-updated', {detail: {error}}));
  });
})();
