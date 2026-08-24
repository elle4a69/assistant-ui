self.__acknowledgedArrivalSessions = self.__acknowledgedArrivalSessions || new Set();

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = {}; }
  if (payload.type === 'customer-arrival-cleared' && payload.sessionId) {
    self.__acknowledgedArrivalSessions.add(payload.sessionId);
    event.waitUntil(Promise.all([
      self.registration.getNotifications({ tag: payload.tag || `arrival-${payload.sessionId}` }).then(notifications => {
        notifications.forEach(notification => notification.close());
      }),
      Number(payload.remainingCount) > 0 && self.navigator.setAppBadge
        ? self.navigator.setAppBadge(Number(payload.remainingCount))
        : self.navigator.clearAppBadge
          ? self.navigator.clearAppBadge()
          : Promise.resolve(),
    ]));
    return;
  }
  if (payload.type === 'customer-arrival' && self.__acknowledgedArrivalSessions.has(payload.sessionId)) {
    return;
  }
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

self.addEventListener('message', (event) => {
  if (event.data?.type !== 'arrival-acknowledged' || !event.data.sessionId) return;
  self.__acknowledgedArrivalSessions.add(event.data.sessionId);
  const tag = `arrival-${event.data.sessionId}`;
  event.waitUntil(Promise.all([
    self.registration.getNotifications({ tag }).then(notifications => {
      notifications.forEach(notification => notification.close());
    }),
    typeof event.data.remainingCount === 'number'
      ? (event.data.remainingCount > 0 && self.navigator.setAppBadge
          ? self.navigator.setAppBadge(event.data.remainingCount)
          : self.navigator.clearAppBadge
            ? self.navigator.clearAppBadge()
            : Promise.resolve())
      : Promise.resolve(),
  ]));
});
