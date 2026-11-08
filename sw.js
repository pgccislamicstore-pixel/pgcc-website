/* PGCC app service worker: works offline for the app pages */
var C = 'pgcc-app-v1';
var FILES = ['./', 'index.html', 'manifest.webmanifest', 'data.json'];
self.addEventListener('install', function (e) { e.waitUntil(caches.open(C).then(function (c) { return c.addAll(FILES); }).catch(function () {})); self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== C; }).map(function (k) { return caches.delete(k); })); })); self.clients.claim(); });
self.addEventListener('fetch', function (e) {
  var u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(function (r) { var cp = r.clone(); caches.open(C).then(function (c) { c.put(e.request, cp); }); return r; }).catch(function () { return caches.match(e.request); }));
});
