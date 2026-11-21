const CACHE_NAME = 'mwangi-inventory-v9';
const APP_SHELL = [
  './', './styles.css', './script.js', './manifest.json',
  './icon.svg', './icons/icon-192.png', './icons/icon-512.png'
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      if (res.ok) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(e.request, res.clone());
      }
      return res;
    } catch {
      const cached = await caches.match(e.request);
      if (cached) return cached;
      if (e.request.mode === 'navigate') {
        const shell = await caches.match('./');
        if (shell) return shell;
      }
      return Response.error();
    }
  })());
});