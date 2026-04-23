// AM Hub Sync — background service worker
// Каждые 30 минут получает данные из Merchrules и отправляет в AM Hub

const MR_BASE = "https://merchrules.any-platform.ru";
const MR_LOGIN_URL = `${MR_BASE}/backend-v2/auth/login`;

// Ключи, которые хранятся в storage.sync (переживают переустановку/смену устройства)
const SYNC_KEYS    = ["mr_login", "mr_password", "hub_url", "hub_token"];
// Ключи статуса — только local (большие, часто меняются, не нужна синхронизация)
const LOCAL_KEYS   = ["last_sync", "last_sync_result", "sync_status", "sync_error", "sync_log"];
const SYNC_LOG_KEY = "sync_log";
const SYNC_LOG_MAX = 10;

// Флаг защиты от параллельных синков
let _syncRunning = false;

// ── Хранилище ──────────────────────────────────────────────────────────────

async function getSettings() {
  const [syncData, localData] = await Promise.all([
    new Promise(r => chrome.storage.sync.get(SYNC_KEYS, r)),
    new Promise(r => chrome.storage.local.get(LOCAL_KEYS, r)),
  ]);
  return { ...syncData, ...localData };
}

async function saveCreds(data) {
  // Креды → sync, статус → local
  const syncPart  = {};
  const localPart = {};
  for (const [k, v] of Object.entries(data)) {
    if (SYNC_KEYS.includes(k)) syncPart[k]  = v;
    else                        localPart[k] = v;
  }
  const ops = [];
  if (Object.keys(syncPart).length)  ops.push(new Promise(r => chrome.storage.sync.set(syncPart, r)));
  if (Object.keys(localPart).length) ops.push(new Promise(r => chrome.storage.local.set(localPart, r)));
  await Promise.all(ops);
}

async function saveStatus(data) {
  return new Promise(r => chrome.storage.local.set(data, r));
}

// ── Авторизация в Merchrules ────────────────────────────────────────────────

async function mrAuth(login, password) {
  const fields = ["email", "login", "username"];
  const modes  = ["json", "form"];
  for (const field of fields) {
    for (const mode of modes) {
      try {
        const resp = await fetch(MR_LOGIN_URL, {
          method: "POST",
          headers: mode === "json"
            ? { "Content-Type": "application/json" }
            : { "Content-Type": "application/x-www-form-urlencoded" },
          body: mode === "json"
            ? JSON.stringify({ [field]: login, password })
            : new URLSearchParams({ [field]: login, password }).toString(),
        });
        if (resp.ok) {
          let token = null;
          try {
            const data = await resp.json();
            token = data.token || data.access_token || data.accessToken || data.jwt;
            if (!token) {
              for (const wrap of ["data", "result", "payload"]) {
                const inner = data[wrap];
                if (inner && typeof inner === "object") {
                  token = inner.token || inner.access_token || inner.accessToken;
                  if (token) break;
                }
              }
            }
          } catch (_) {}
          if (token) return { token, field, mode };
        }
      } catch (e) {
        console.warn(`mrAuth field=${field} mode=${mode} error:`, e);
      }
    }
  }
  throw new Error("Merchrules auth failed — проверьте логин/пароль");
}

// ── Получить список аккаунтов ───────────────────────────────────────────────

async function mrGetAccounts(token) {
  const endpoints = [
    `${MR_BASE}/backend-v2/accounts`,
    `${MR_BASE}/backend-v2/accounts?limit=500`,
    `${MR_BASE}/backend-v2/sites`,
    `${MR_BASE}/backend-v2/sites?limit=500`,
  ];
  for (const url of endpoints) {
    try {
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) continue;
      const data = await resp.json();
      const list = data.accounts || data.sites || data.items || data.results
        || (Array.isArray(data) ? data : null);
      if (list && list.length) return list;
    } catch (_) {}
  }
  return [];
}

// ── Задачи одного сайта ─────────────────────────────────────────────────────

async function mrGetTasks(token, siteId) {
  try {
    const resp = await fetch(
      `${MR_BASE}/backend-v2/tasks?site_id=${siteId}&status=plan,in_progress,blocked&limit=100`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.tasks || data.items || (Array.isArray(data) ? data : []);
  } catch (_) { return []; }
}

// ── Встречи одного сайта ────────────────────────────────────────────────────

async function mrGetMeetings(token, siteId) {
  try {
    const resp = await fetch(
      `${MR_BASE}/backend-v2/meetings?site_id=${siteId}&limit=20`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.meetings || data.items || (Array.isArray(data) ? data : []);
  } catch (_) { return []; }
}

// ── Метрики сайта ───────────────────────────────────────────────────────────

async function mrGetMetrics(token, siteId) {
  for (const path of [
    `/backend-v2/sites/${siteId}/analytics`,
    `/backend-v2/sites/${siteId}/stats`,
  ]) {
    try {
      const resp = await fetch(`${MR_BASE}${path}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (resp.ok) return await resp.json();
    } catch (_) {}
  }
  return null;
}

// ── Основной sync ───────────────────────────────────────────────────────────

async function doSync(manual = false) {
  // Защита от параллельных запусков
  if (_syncRunning) {
    console.log("AM Hub sync: already running, skip");
    return { ok: false, error: "already_running" };
  }
  _syncRunning = true;

  try {
    return await _doSyncInner(manual);
  } finally {
    _syncRunning = false;
  }
}

async function _doSyncInner(manual) {
  const settings = await getSettings();
  const { mr_login, mr_password, hub_url, hub_token } = settings;

  if (!mr_login || !mr_password) {
    await saveStatus({ sync_status: "error", sync_error: "Не настроены креды Merchrules" });
    return { ok: false, error: "no_creds" };
  }
  if (!hub_url || !hub_token) {
    await saveStatus({ sync_status: "error", sync_error: "Не настроен AM Hub URL/токен" });
    return { ok: false, error: "no_hub" };
  }

  // Валидация hub_url
  const cleanHub = hub_url.trim().replace(/\/$/, "");
  if (!cleanHub.startsWith("https://") && !cleanHub.startsWith("http://")) {
    await saveStatus({ sync_status: "error", sync_error: "Hub URL должен начинаться с https://" });
    return { ok: false, error: "bad_url" };
  }

  await saveStatus({ sync_status: "running", sync_error: null });

  try {
    // 1. Авторизация в Merchrules
    const { token: mrToken } = await mrAuth(mr_login, mr_password);

    // 2. Список аккаунтов
    const accounts = await mrGetAccounts(mrToken);
    if (!accounts.length) throw new Error("Нет аккаунтов в Merchrules — укажите site_ids в AM Hub");

    // 3. Для каждого аккаунта — задачи, встречи, метрики
    const payload = { accounts: [] };

    for (const acc of accounts) {
      const siteId = String(acc.id || acc.site_id || "");
      if (!siteId) continue;

      const [tasks, meetings, metrics] = await Promise.all([
        mrGetTasks(mrToken, siteId),
        mrGetMeetings(mrToken, siteId),
        mrGetMetrics(mrToken, siteId),
      ]);

      payload.accounts.push({
        id: siteId,
        name: acc.name || acc.title || `Site ${siteId}`,
        segment: acc.segment || acc.tariff || null,
        domain: acc.domain || acc.url || null,
        health_score: acc.health_score || acc.healthScore || null,
        tasks: tasks.map(t => ({
          id: String(t.id || ""),
          title: t.title || t.name || "",
          status: t.status || "plan",
          priority: t.priority || "medium",
          due_date: t.due_date || t.dueDate || null,
          team: t.team || t.assignee || null,
          task_type: t.type || t.task_type || null,
        })),
        meetings: meetings.map(m => ({
          id: String(m.id || ""),
          date: m.date || m.meeting_date || m.createdAt || null,
          type: m.type || m.meeting_type || "meeting",
          title: m.title || m.name || null,
          summary: m.summary || m.description || null,
        })),
        metrics: metrics || null,
      });
    }

    // 4. Отправляем в AM Hub
    const hubResp = await fetch(`${cleanHub}/api/sync/extension`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${hub_token}`,
      },
      body: JSON.stringify(payload),
    });

    if (hubResp.status === 401) {
      throw new Error("AM Hub: токен недействителен — обновите токен в настройках расширения");
    }
    if (!hubResp.ok) {
      const errText = await hubResp.text();
      throw new Error(`AM Hub error ${hubResp.status}: ${errText.slice(0, 200)}`);
    }

    const result = await hubResp.json();

    await saveStatus({
      sync_status: "ok",
      sync_error: null,
      last_sync: Date.now(),
      last_sync_result: result,
    });
    await appendLog({
      ts: Date.now(),
      tone: "ok",
      message: `hub: ${result.clients_synced || 0} кл, ${result.tasks_synced || 0} зад, ${result.meetings_synced || 0} встреч`,
    });

    if (manual) {
      chrome.notifications.create("sync_ok_" + Date.now(), {
        type: "basic",
        iconUrl: "icon48.png",
        title: "AM Hub Sync",
        message: `✅ Синхронизировано: ${result.clients_synced || 0} клиентов, ${result.tasks_synced || 0} задач`,
      });
    }

    console.log("AM Hub sync OK:", result);
    return { ok: true, result };

  } catch (err) {
    console.error("AM Hub sync error:", err);
    await saveStatus({ sync_status: "error", sync_error: err.message });
    await appendLog({
      ts: Date.now(),
      tone: "error",
      message: String(err.message || "sync failed").slice(0, 140),
    });

    if (manual) {
      chrome.notifications.create("sync_err_" + Date.now(), {
        type: "basic",
        iconUrl: "icon48.png",
        title: "AM Hub Sync — Ошибка",
        message: err.message.slice(0, 100),
      });
    }

    return { ok: false, error: err.message };
  }
}

// ── Log (последние 10 событий) ──────────────────────────────────────────────

async function appendLog(entry) {
  try {
    const cur = await new Promise(r => chrome.storage.local.get([SYNC_LOG_KEY], r));
    const list = Array.isArray(cur[SYNC_LOG_KEY]) ? cur[SYNC_LOG_KEY] : [];
    list.unshift(entry);
    if (list.length > SYNC_LOG_MAX) list.length = SYNC_LOG_MAX;
    await saveStatus({ [SYNC_LOG_KEY]: list });
  } catch (e) { /* silent */ }
}

// ── Алармы (расписание) ─────────────────────────────────────────────────────

chrome.alarms.create("sync", { periodInMinutes: 30 });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "sync") doSync(false);
});

// ── Сообщения из popup ──────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === "sync") {
    doSync(true).then(sendResponse);
    return true; // async response
  }
  if (msg.action === "getStatus") {
    getSettings().then(s => sendResponse({
      status: s.sync_status || "idle",
      error: s.sync_error || null,
      last_sync: s.last_sync || null,
      last_result: s.last_sync_result || null,
      log: Array.isArray(s[SYNC_LOG_KEY]) ? s[SYNC_LOG_KEY] : [],
    }));
    return true;
  }
  if (msg.action === "clearLog") {
    saveStatus({ [SYNC_LOG_KEY]: [] }).then(() => sendResponse({ ok: true }));
    return true;
  }
});
