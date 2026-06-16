"use strict";
// Minimal service worker: cache the app shell for offline/fast load.
// API calls (/v1/*) and non-GET requests always go to the network.

const CACHE = "phx-cognition-v1";
const SHELL = [
  "/cognition",
  "/cognition/static/index.html",
  "/cognition/static/app.js",
  "/cognition/static/app.css",
  "/cognition/static/manifest.webmanifest",
  "/cognition/static/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/v1/")) return;
  event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
});
