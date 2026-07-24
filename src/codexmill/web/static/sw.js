// CodexMill service worker — caches the app shell so the UI installs + loads offline.
// Generation still needs the network (it calls your AI engine); API calls are never cached.
const CACHE = "codexmill-v13";
const SHELL = [
  "/",
  "/app.css?v=9",
  "/sample.md",
  "/icon-192.png",
  "/icon-512.png",
  "/fonts/newsreader-variable.woff2",
  "/fonts/newsreader-italic-variable.woff2",
  "/fonts/archivo-variable.woff2",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never intercept the API or non-GET requests — always straight to the network.
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) return;
  // Page loads: network-first (so updates show), fall back to the cached shell offline.
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/")));
    return;
  }
  // CSS/JS: network-first so style + logic changes always show; fall back to cache offline. (These
  // change often; serving them cache-first is what made earlier UI fixes appear not to land.)
  if (url.pathname.endsWith(".css") || url.pathname.endsWith(".js")) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { const c = r.clone(); caches.open(CACHE).then((x) => x.put(e.request, c)); return r; })
        .catch(() => caches.match(e.request)),
    );
    return;
  }
  // Other static assets (fonts, icons, sample): cache-first, then network.
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
