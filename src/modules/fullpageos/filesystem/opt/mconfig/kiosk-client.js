(() => {
  if (location.pathname.startsWith('/auth/')) return;
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
})();
