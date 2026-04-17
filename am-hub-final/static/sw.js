/**
 * AM Hub Service Worker v2
 * Offline поддержка + кеш статики + IndexedDB для данных клиентов
 */

const CACHE_NAME  = "amhub-v2";
const STATIC_URLS = ["/static/css/style.css", "/login"];
const IDB_NAME    = "amhub-offline";
const IDB_VERSION = 1;

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  // Статика — cache first
  if (event.request.method === "GET" && url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(resp => {
          if (resp.ok) {
            caches.open(CACHE_NAME).then(c => c.put(event.request, resp.clone()));
          }
          return resp;
        }).catch(() => new Response("offline", {status: 503}));
      })
    );
    return;
  }

  // API клиентов — network first, IndexedDB fallback
  if (event.request.method === "GET" && url.pathname.startsWith("/api/cabinet/my-clients")) {
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) {
            resp.clone().json().then(data => {
              if (data.clients) saveToIDB("my-clients", data);
            });
          }
          return resp;
        })
        .catch(() =>
          getFromIDB("my-clients").then(data => {
            if (data) return new Response(JSON.stringify(data), {
              headers: {"Content-Type": "application/json", "X-Served-From": "idb-cache"}
            });
            return new Response(JSON.stringify({clients: [], offline: true}), {
              headers: {"Content-Type": "application/json"}
            });
          })
        )
    );
    return;
  }

  // /api/stats — network first с IDB fallback
  if (event.request.method === "GET" && url.pathname === "/api/stats") {
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) resp.clone().json().then(d => saveToIDB("stats", d));
          return resp;
        })
        .catch(() =>
          getFromIDB("stats").then(d =>
            new Response(JSON.stringify(d || {overdue:0,warning:0,open_tasks:0,offline:true}),
              {headers: {"Content-Type": "application/json"}})
          )
        )
    );
    return;
  }
});

// ── IndexedDB helpers ─────────────────────────────────────────────────────────
function openIDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains("cache")) {
        db.createObjectStore("cache", {keyPath: "key"});
      }
    };
    req.onsuccess  = e => resolve(e.target.result);
    req.onerror    = e => reject(e.target.error);
  });
}

async function saveToIDB(key, value) {
  try {
    const db = await openIDB();
    const tx = db.transaction("cache", "readwrite");
    tx.objectStore("cache").put({key, value, ts: Date.now()});
  } catch (e) {}
}

async function getFromIDB(key) {
  try {
    const db = await openIDB();
    return new Promise((resolve, reject) => {
      const tx  = db.transaction("cache", "readonly");
      const req = tx.objectStore("cache").get(key);
      req.onsuccess = e => {
        const entry = e.target.result;
        // Данные живут 4 часа
        if (entry && Date.now() - entry.ts < 4 * 3600 * 1000) {
          resolve(entry.value);
        } else {
          resolve(null);
        }
      };
      req.onerror = () => resolve(null);
    });
  } catch (e) {
    return null;
  }
}

// ── Push notifications ────────────────────────────────────────────────────────
self.addEventListener("push", event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || "AM Hub", {
      body:  data.body  || "",
      icon:  "/static/icon-192.png",
      badge: "/static/icon-72.png",
      tag:   data.tag   || "amhub",
      data:  {url: data.url || "/"},
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(clients.openWindow(url));
});

// ── Background sync ───────────────────────────────────────────────────────────
self.addEventListener("sync", event => {
  if (event.tag === "amhub-sync") {
    event.waitUntil(
      fetch("/api/sync/merchrules", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})
        .catch(() => {})
    );
  }
});
