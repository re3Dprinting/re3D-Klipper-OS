(() => {
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

  async function request(message) {
    const result = await chrome.runtime.sendMessage(message);
    if (!result || result.error) throw new Error(result?.error || 'The touchscreen browser did not respond.');
    return result;
  }

  if (local) {
    document.addEventListener('click', async event => {
      const link = event.target.closest?.('a[href]');
      if (!link || !link.closest('#news-page') || event.defaultPrevented
          || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(link.href, location.href);
      if (url.protocol !== 'https:' || url.origin === location.origin) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      try {
        await request({type: 're3d-open-external', url: url.href});
      } catch (error) {
        let notice = document.getElementById('news-external-error');
        if (!notice) {
          notice = document.createElement('p');
          notice.id = 'news-external-error';
          notice.setAttribute('role', 'alert');
          document.getElementById('news-page').prepend(notice);
        }
        notice.textContent = 'Could not open this page: ' + error.message;
      }
    }, {capture: true});
    return;
  }
  if (top !== self) return;

  (async () => {
    const context = await request({type: 're3d-external-context'});
    if (!context.managed) return;
    const host = document.createElement('div');
    host.id = 're3d-external-navigation';
    const shadow = host.attachShadow({mode: 'open'});
    const style = document.createElement('style');
    style.textContent = `
      :host{all:initial!important;position:fixed!important;top:12px!important;left:12px!important;
        z-index:2147483647!important;max-width:calc(100vw - 24px)!important}
      button{min-height:48px;padding:12px 18px;border:2px solid #181b20;border-radius:10px;
        background:#FFD400;color:#181b20;font:600 16px/1.4 Arial,sans-serif;cursor:pointer;
        box-shadow:0 4px 16px #0006}
      button:hover{background:#FFE04D}
      button:focus-visible{outline:3px solid #181b20;outline-offset:3px}
      button:disabled{cursor:wait}
      p{max-width:320px;margin:8px 0 0;padding:12px;border-radius:8px;background:#181b20;
        color:#F2F2F2;font:14px/1.5 Arial,sans-serif}
      [hidden]{display:none}
    `;
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '\u2190 Back to interface';
    const errorNotice = document.createElement('p');
    errorNotice.setAttribute('role', 'alert');
    errorNotice.hidden = true;
    button.addEventListener('click', async () => {
      button.disabled = true;
      errorNotice.hidden = true;
      try {
        await request({type: 're3d-return-to-interface'});
      } catch (error) {
        errorNotice.textContent = 'Could not return to the interface: ' + error.message + ' Please try again.';
        errorNotice.hidden = false;
        button.disabled = false;
      }
    });
    shadow.append(style, button, errorNotice);
    document.documentElement.appendChild(host);
  })().catch(error => console.error('Could not initialize touchscreen return controls:', error));
})();
