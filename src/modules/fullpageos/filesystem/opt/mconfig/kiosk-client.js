(async () => {
  if (location.pathname.startsWith('/auth/')) return;
  const marker = 'data-re3d-kiosk-client';
  if (document.documentElement.hasAttribute(marker)) return;
  document.documentElement.setAttribute(marker, '');
  const response = await fetch('/auth/status', {cache:'no-store'});
  if (!response.ok) throw new Error('Could not check touchscreen security mode');
  const state = await response.json();
  if (!state.local) return;
  if (!state.touchscreen_locked) {
    addEventListener('click', event => {
      const link = event.target.closest?.('a[href]');
      if (!link || event.defaultPrevented) return;
      const url = new URL(link.href, location.href);
      if (url.origin === location.origin && url.pathname === '/configurator/') {
        event.preventDefault();
        event.stopImmediatePropagation();
        // A same-origin navigation is intercepted by Mainsail's offline service worker.
        url.port = url.protocol === 'https:' ? '8443' : '8081';
        url.pathname = '/';
        location.assign(url.href);
      }
    }, {capture:true});
    return;
  }
  if (top === self) {
    location.replace('http://localhost/auth/kiosk'
      + (location.port === '8081' ? '?view=configurator' : ''));
    return;
  }
  const active = () => parent.postMessage({type:'re3d-activity'}, 'http://localhost');
  addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.defaultPrevented) return;
    const url = new URL(link.href, location.href);
    if (url.origin === 'http://localhost' && url.pathname === '/configurator/') {
      event.preventDefault();
      event.stopImmediatePropagation();
      parent.postMessage({type:'re3d-configurator'}, 'http://localhost');
    }
  }, {capture:true});
  for (const event of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
    addEventListener(event, active, {capture:true, passive:true});
  }
})().catch(error => {
  document.documentElement.removeAttribute('data-re3d-kiosk-client');
  console.error('Touchscreen navigation setup failed:', error);
});
