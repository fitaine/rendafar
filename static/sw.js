/*
 * Service worker — deliberately does NOT cache anything.
 *
 * It exists only so Chrome offers "Install app" on Android. Every request goes
 * straight to the network, untouched.
 *
 * Caching would be actively dangerous here. This app has one job: telling you
 * whether a render is healthy. A cached page showing "412/900 tiles, all good"
 * for a render that died an hour ago is worse than no page at all, because it
 * is a convincing lie. If the machine is unreachable, the UI must say so — and
 * it does, via the "No connection" state.
 */

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (e) => {
  // Drop any cache a previous version might have left behind.
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  // Pass through. Present so the app is installable, not to serve anything.
  e.respondWith(fetch(e.request));
});
