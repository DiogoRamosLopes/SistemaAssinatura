// AssinaNet Service Worker v1.0
const CACHE_NAME = 'assinanet-v1';
const CACHE_URLS = [
  '/',
  '/admin',
  '/static/manifest.json',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Sora:wght@300;400;600;700&display=swap',
];

// ── Install: cache shell assets ──────────────────────────────────────────────
self.addEventListener('install', event => {
  console.log('[SW] Installing AssinaNet SW...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      // Cache what we can, ignore failures (e.g. fonts offline)
      return Promise.allSettled(
        CACHE_URLS.map(url => cache.add(url).catch(() => console.warn('[SW] Could not cache:', url)))
      );
    }).then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ───────────────────────────────────────────────
self.addEventListener('activate', event => {
  console.log('[SW] Activating...');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for API, cache-first for assets ─────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API calls: always network, never cache
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // PDF downloads: always network
  if (url.pathname.includes('/pdf/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Static assets & pages: network-first, fallback to cache
  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Cache successful GET responses
        if (event.request.method === 'GET' && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => {
        // Offline fallback: serve from cache
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          // Fallback for navigation requests
          if (event.request.mode === 'navigate') {
            return caches.match('/');
          }
          return new Response('Offline — recurso não disponível', { status: 503 });
        });
      })
  );
});

// ── Background Sync (future use) ─────────────────────────────────────────────
self.addEventListener('sync', event => {
  if (event.tag === 'sync-contratos') {
    console.log('[SW] Background sync triggered');
  }
});
