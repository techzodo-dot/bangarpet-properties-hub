/* Bangarpet Property Hub service worker (version {{ version }}).
   Caches static assets and an offline page only. HTML pages always come from
   the network so private pages are never stored on the device. */
"use strict";
const VERSION = "{{ version }}";
const STATIC_CACHE = "bph-static-" + VERSION;
const OFFLINE_URL = "{{ offline_url }}";
const PRECACHE = {{ precache|safe }};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload", credentials: "omit" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("bph-") && k !== STATIC_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Pages: always the network; show the offline page only when there is no connection.
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  // Static assets: serve from cache, refresh in the background.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then((cache) =>
        cache.match(request).then((cached) => {
          const network = fetch(request).then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          }).catch(() => cached);
          return cached || network;
        })
      )
    );
  }
  // Everything else (media, API calls, admin) goes straight to the network.
});
