// Simple pass-through service worker for PWA installation criteria
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Cache-bypass/pass-through
  event.respondWith(fetch(event.request));
});
