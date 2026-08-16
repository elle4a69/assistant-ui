self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = {}; }
  const title = payload.title || 'Tori Operations';
  const options = {
    body: payload.body || 'There is a new operational alert.',
    icon: '/tori_avatar.jpg',
    badge: '/tori_avatar.jpg',
    tag: payload.tag || 'tori-operations',
    renotify: true,
    requireInteraction: payload.type === 'customer-arrival',
    vibrate: [300, 120, 300, 120, 600],
    data: { url: payload.url || '/arrivals', sessionId: payload.sessionId || null },
  };
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, options),
    self.navigator.setAppBadge ? self.navigator.setAppBadge(1) : Promise.resolve(),
  ]));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/arrivals', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async clients => {
    const existing = clients.find(client => new URL(client.url).origin === self.location.origin);
    if (existing) {
      await existing.navigate(target);
      return existing.focus();
    }
    return self.clients.openWindow(target);
  }));
});
