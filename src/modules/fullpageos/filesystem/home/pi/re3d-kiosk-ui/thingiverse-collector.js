(() => {
  function readDesigns() {
    if (!['www.thingiverse.com', 'thingiverse.com'].includes(location.hostname)
        || !/^\/re3dprinting\/designs\/?$/.test(location.pathname)) {
      return {error: 'Thingiverse did not load the re3D designs page.'};
    }
    if (/just a moment|performing security verification|verify (?:that )?you are human|checking your browser/i
        .test(document.title + ' ' + (document.body?.innerText || '').slice(0, 2000))) {
      return {pending: true, error: 'Thingiverse is showing a verification page. Saved designs have been kept.'};
    }
    const designs = [];
    const seen = new Set();
    for (const card of document.querySelectorAll('.item-card-container')) {
      const links = [...card.querySelectorAll('a[href]')];
      if (!links.some(link => new URL(link.href).pathname.toLowerCase() === '/re3dprinting/designs')) continue;
      const photoLink = links.find(link => /^\/thing:\d+$/.test(new URL(link.href).pathname)
        && link.querySelector('img'));
      if (!photoLink || seen.has(photoLink.href)) continue;
      const titleLink = links.find(link => link.href === photoLink.href && !link.querySelector('img')
        && link.textContent.trim());
      const image = photoLink.querySelector('img').src;
      if (!titleLink || !image.startsWith('https://')) continue;
      seen.add(photoLink.href);
      designs.push({title: titleLink.textContent.replace(/\s+/g, ' ').trim(), image});
      if (designs.length === 6) break;
    }
    return {designs};
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === 're3d-read-designs') respond(readDesigns());
  });

  let publishing = false;
  const observer = new MutationObserver(publishDisplayedDesigns);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  function publishDisplayedDesigns() {
    if (publishing) return;
    const result = readDesigns();
    if (!result.designs?.length) return;
    publishing = true;
    chrome.runtime.sendMessage({type: 're3d-publish-designs', designs: result.designs}).then(response => {
      if (!response || response.error) throw new Error(response?.error || 'The design cache did not respond.');
      observer.disconnect();
    }).catch(error => {
      observer.disconnect();
      console.error('Could not save displayed Thingiverse designs:', error);
    });
  }
  publishDisplayedDesigns();
})();
