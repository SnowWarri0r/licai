// Service Worker for PWA offline support
// IMPORTANT: bump CACHE_NAME on each release to force activate→clear-old-cache
const CACHE_NAME = 'licai-v53';
const STATIC_ASSETS = ['/manifest.json', '/icon-192.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = e.request.url;

  // 1) API / WS — pass through, never cache
  if (url.includes('/api/') || url.includes('/ws')) return;

  // 2) Navigation / HTML / SW — always fetch from network so new bundle hashes
  //    are picked up immediately. Falls back to cached '/' offline.
  if (
    e.request.mode === 'navigate' ||
    url.endsWith('/') ||
    url.endsWith('/index.html') ||
    url.endsWith('/sw.js')
  ) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' }).catch(() => caches.match('/'))
    );
    return;
  }

  // 3) Hashed assets (/assets/index-xxxx.js etc.) — cache-first is safe since
  //    the filename hash changes on every build.
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
      return res;
    }))
  );
});
