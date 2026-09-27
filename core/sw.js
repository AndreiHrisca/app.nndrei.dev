/* Increment on every release that changes static assets or the offline page. */
const CACHE_VERSION = 'v3';
const CACHE_PREFIX = 'nndrei-pwa-';
const CACHE_NAME = CACHE_PREFIX + CACHE_VERSION;
const OFFLINE_URL = '/offline/';
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const response = await fetch(OFFLINE_URL, {credentials: 'omit', cache: 'reload'});
    if (!response.ok || response.redirected) throw new Error('Offline page unavailable');
    const cache = await caches.open(CACHE_NAME);
    await cache.put(OFFLINE_URL, response);
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});
self.addEventListener('message', event => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  // Bypass before any cache access. All non-static data is network-only.
  if (request.method !== 'GET' || request.headers.has('HX-Request') ||
      url.origin !== self.location.origin ||
      /^\/(admin|login|logout|2fa|recuperar|invitacion)(\/|$)/.test(url.pathname)) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request, {cache: 'no-store'}).catch(() => caches.match(OFFLINE_URL, {cacheName: CACHE_NAME})));
    return; // Never persist navigation HTML, authenticated or otherwise.
  }
  if (!url.pathname.startsWith('/static/') || !/\.(css|js|woff2?|ttf|otf|png|svg|ico|webp|jpe?g)$/.test(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    const type = response.headers.get('Content-Type') || '';
    if (response.ok && !response.redirected && /^(text\/css|(?:application|text)\/javascript|font\/|image\/|application\/(?:font|x-font))/.test(type)) {
      await cache.put(request, response.clone());
    }
    return response;
  })());
});
