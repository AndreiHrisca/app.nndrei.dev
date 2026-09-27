(() => {
  const mode = matchMedia('(display-mode: standalone)');
  const standalone = () => document.body.classList.toggle('standalone', mode.matches || navigator.standalone === true);
  standalone();
  mode.addEventListener('change', standalone);
  document.addEventListener('htmx:afterSwap', standalone);
  // Delegation also covers links inserted by HTMX.
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    const url = new URL(link.href, location.href);
    if (['tel:', 'mailto:'].includes(url.protocol) || (['http:', 'https:'].includes(url.protocol) && url.origin !== location.origin)) {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    }
  }, true);
  if (!('serviceWorker' in navigator)) return;
  let reloading = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloading) location.reload();
  });
  navigator.serviceWorker.register('/sw.js', {scope: '/', updateViaCache: 'none'}).then(registration => {
    const offerUpdate = () => {
      if (!registration.waiting || !navigator.serviceWorker.controller) return;
      const notice = document.getElementById('pwa-update');
      const button = document.getElementById('pwa-reload');
      if (!notice || !button) return;
      notice.hidden = false;
      button.onclick = () => {
        reloading = true;
        registration.waiting?.postMessage({type: 'SKIP_WAITING'});
      };
    };
    offerUpdate();
    registration.addEventListener('updatefound', () => {
      registration.installing?.addEventListener('statechange', offerUpdate);
    });
    document.addEventListener('htmx:afterSwap', offerUpdate);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') registration.update().catch(() => {});
    });
  }).catch(() => { /* The online app remains usable if installation fails. */ });
})();
