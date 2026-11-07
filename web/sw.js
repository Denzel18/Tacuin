/* Tacuìn: service worker per le notifiche (nessuna cache: i dati restano sempre quelli del server) */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Tacuìn', {
    body: d.body || '',
    icon: 'vendor/brand/icon-192.png',
    badge: 'vendor/brand/badge-96.png',
    tag: d.tag || undefined,
    renotify: !!d.tag,
    lang: 'it',
    data: {url: typeof d.url === 'string' && d.url.startsWith('/') ? d.url : '/'}
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL(e.notification.data && e.notification.data.url || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    for (const c of list) {
      if (new URL(c.url).origin === self.location.origin) {
        return c.focus().then(w => (w && w.navigate ? w.navigate(url) : null)).catch(() => self.clients.openWindow(url));
      }
    }
    return self.clients.openWindow(url);
  }));
});
