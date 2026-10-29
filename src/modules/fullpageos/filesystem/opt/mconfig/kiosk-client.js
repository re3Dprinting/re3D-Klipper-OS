(() => {
  if (location.pathname.startsWith('/auth/')) return;
  if (top === self) {
    location.replace('/auth/kiosk');
    return;
  }
  const active = () => parent.postMessage({type:'re3d-activity'}, 'http://localhost');
  for (const event of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
    addEventListener(event, active, {capture:true, passive:true});
  }
})();
