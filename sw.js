// Modus Mobile — Service Worker
// Estrategia: network-first para todo lo propio; sin caché para Supabase.
// Sube BUILD en cada despliegue para invalidar la caché anterior.
const BUILD = '2026-11-12-3';
const CACHE_NAME = `modus-shell-${BUILD}`;
const ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-180.png',
  './icon-512.png',
  './icon-maskable-192.png',
  './icon-maskable-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // Supabase y terceros: sin intermediación

  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, './index.html', false));
  } else {
    event.respondWith(networkFirst(request, null, true));
  }
});

async function networkFirst(request, fallbackUrl, storeInCache) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const fresh = await fetch(request);
    if (storeInCache && fresh && fresh.ok) {
      await cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    if (fallbackUrl) {
      const fallback = await cache.match(fallbackUrl);
      if (fallback) return fallback;
    }
    return new Response('Sin conexión', { status: 503, statusText: 'Offline' });
  }
}
