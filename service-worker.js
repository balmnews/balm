const CACHE_NAME = 'balm-v1';
const ASSET_CACHE = 'balm-assets-v1';

// Static assets — cache-first
const PRECACHE_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
];

// Install: precache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(ASSET_CACHE).then(cache => cache.addAll(PRECACHE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: remove old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME && key !== ASSET_CACHE)
          .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for HTML digests, cache-first for assets
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  const isHtml = url.pathname.endsWith('.html') || url.pathname === '/' || url.pathname === '';
  const isAudio = url.pathname.endsWith('.mp3');

  if (isAudio) {
    // Don't cache audio — too large
    return;
  }

  if (isHtml) {
    // Network-first for digest HTML files
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  } else {
    // Cache-first for fonts, JS, JSON, etc.
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(ASSET_CACHE).then(cache => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
  }
});
