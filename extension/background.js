// AM Hub Sync — background service worker
// Каждые 30 минут получает данные из Merchrules и отправляет в AM Hub

const MR_BASE = "https://merchrules.any-platform.ru";
const MR_LOGIN_URL = `${MR_BASE}/backend-v2/auth/login`;

// ── Получить настройки из storage ──────────────────────────────────────────

async function getSettings() {
  return new Promise(resolve => {
    chrome.storage.local.get(
      ["mr_login", "mr_password", "hub_url", "hub_token", "last_sync", "last_sync_result", "sync_status", "sync_error", "sync_log"],
      resolve
    );
  });
}

async function saveSettings(data) {
  return new Promise(resolve => chrome.storage.local.set(data, resolve));
}

// ── Авторизация в Merchrules ────────────────────────────────────────────────

async function mrAuth(login, password) {
  const fields = ["email", "login", "username"];
  for (const field of fields) {
    try {
      const resp = await fetch(MR_LOGIN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: login, password }),
      });
      if (resp.ok) {
        const data = await resp.json();
        const token = data.token || data.access_token || data.accessToken;
        if (token) return { token, field };
      }
    } catch (e) {
      console.warn(`mrAuth field=${field} error:`, e);
    }
  }
  throw new Error("Merchrules auth failed — проверьте логин/пароль");
}

// ── Получить список аккаунтов ───────────────────────────────────────────────

async function mrGetAccounts(token) {
  const resp = await fetch(`${MR_BASE}/backend-v2/accounts`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`accounts HTTP ${resp.status}`);
  const data = await resp.json();
  return data.accounts || data.items || (Array.isArray(data) ? data : []);
}

// ── Задачи одного сайта ─────────────────────────────────────────────────────

async function mrGetTasks(token, siteId) {
  const resp = await fetch(
    `${MR_BASE}/backend-v2/tasks?site_id=${siteId}&status=plan,in_progress,blocked&limit=100`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!resp.ok) return [];
  const data = await resp.json();
  return data.tasks || data.items || (Array.isArray(data) ? data : []);
}

// ── Встречи одного сайта ────────────────────────────────────────────────────

async function mrGetMeetings(token, siteId) {
  const resp = await fetch(
    `${MR_BASE}/backend-v2/meetings?site_id=${siteId}&limit=20`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!resp.ok) return [];
  const data = await resp.json();
  return data.meetings || data.items || (Array.isArray(data) ? data : []);
}

// ── Метрики сайта ───────────────────────────────────────────────────────────

async function mrGetMetrics(token, siteId) {
  for (const path of [`/backend-v2/sites/${siteId}/analytics`, `/backend-v2/sites/${siteId}/stats`]) {
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
  const settings = await getSettings();
  const { mr_login, mr_password, hub_url, hub_token } = settings;

  if (!mr_login || !mr_password) {
    await saveSettings({ sync_status: "error", sync_error: "Не настроены креды Merchrules" });
    return { ok: false, error: "no_creds" };
  }
  if (!hub_url || !hub_token) {
    await saveSettings({ sync_status: "error", sync_error: "Не настроен AM Hub URL/токен" });
    return { ok: false, error: "no_hub" };
  }

  await saveSettings({ sync_status: "running", sync_error: null });

  try {
    // 1. Авторизация в Merchrules
    const { token: mrToken } = await mrAuth(mr_login, mr_password);

    // 2. Список аккаунтов
    const accounts = await mrGetAccounts(mrToken);
    if (!accounts.length) throw new Error("Нет аккаунтов в Merchrules");

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
    const hubResp = await fetch(`${hub_url}/api/sync/extension`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${hub_token}`,
      },
      body: JSON.stringify(payload),
    });

    if (!hubResp.ok) {
      const errText = await hubResp.text();
      throw new Error(`AM Hub error ${hubResp.status}: ${errText.slice(0, 200)}`);
    }

    const result = await hubResp.json();
    const now = new Date().toLocaleString("ru-RU");

    await saveSettings({
      sync_status: "ok",
      sync_error: null,
      last_sync: Date.now(),   // timestamp для humanAgo / fmtTime
      last_sync_result: result,
    });
    await appendLog({
      ts: Date.now(),
      tone: "ok",
      message: `hub: ${result.clients_synced || 0} клиентов, ${result.tasks_synced || 0} задач`,
    });

    if (manual) {
      chrome.notifications.create({
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
    await saveSettings({ sync_status: "error", sync_error: err.message });
    await appendLog({
      ts: Date.now(),
      tone: "error",
      message: String(err.message || "sync failed").slice(0, 140),
    });

    if (manual) {
      chrome.notifications.create({
        type: "basic",
        iconUrl: "icon48.png",
        title: "AM Hub Sync — Ошибка",
        message: err.message.slice(0, 100),
      });
    }

    return { ok: false, error: err.message };
  }
}

// ── Алармы (расписание) ─────────────────────────────────────────────────────

chrome.alarms.create("sync", { periodInMinutes: 30 });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "sync") doSync(false);
});

// ── Сообщения из popup ──────────────────────────────────────────────────────

// ── Log (последние 10 событий, хранится в chrome.storage) ───────────────────
const SYNC_LOG_KEY = "sync_log";
const SYNC_LOG_MAX = 10;

async function appendLog(entry) {
  try {
    const cur = await new Promise(r => chrome.storage.local.get([SYNC_LOG_KEY], r));
    const list = Array.isArray(cur[SYNC_LOG_KEY]) ? cur[SYNC_LOG_KEY] : [];
    list.unshift(entry);
    if (list.length > SYNC_LOG_MAX) list.length = SYNC_LOG_MAX;
    await saveSettings({ [SYNC_LOG_KEY]: list });
  } catch (e) { /* silent */ }
}

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
    saveSettings({ [SYNC_LOG_KEY]: [] }).then(() => sendResponse({ ok: true }));
    return true;
  }
});
