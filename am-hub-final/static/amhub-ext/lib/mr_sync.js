/**
 * mr_sync.js — Merchrules синхронизация
 * Портировано из старого AM Hub Sync расширения.
 */

import { CONFIG } from "./config.js";
import { syncAccounts } from "./hub.js";

// Перебираем все известные базы — prod и QA. У пользователя может быть
// доступ только к одной, поэтому пробуем обе.
const MR_BASES = [
  "https://merchrules.any-platform.ru",
  "https://merchrules-qa.any-platform.ru",
];
let MR_BASE = MR_BASES[0];  // обновляется на тот, что сработал

// Куда принимать токен из ответа (может называться по-разному у разных бэков).
const TOKEN_KEYS = ["token", "access_token", "accessToken", "jwt", "authToken", "auth_token", "sessionId", "session_id"];

// Возможные пути auth-эндпоинта. Добавляем варианты — вдруг сменили.
const AUTH_PATHS = [
  "/backend-v2/auth/login",
  "/backend/auth/login",
  "/api/auth/login",
  "/auth/login",
];

async function _tryLoginOnce(base, path, field, mode /* "json"|"form" */) {
  const url = `${base}${path}`;
  let headers, body;
  if (mode === "form") {
    headers = { "Content-Type": "application/x-www-form-urlencoded" };
    body = new URLSearchParams({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD }).toString();
  } else {
    headers = { "Content-Type": "application/json" };
    body = JSON.stringify({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD });
  }
  const r = await fetch(url, { method: "POST", headers, body, credentials: "include" });
  if (!r.ok) {
    let bodyText = "";
    try { bodyText = (await r.text()).slice(0, 400); } catch {}
    return { ok: false, status: r.status, body: bodyText, url };
  }
  // Token может прийти в JSON, в Authorization, или как Set-Cookie (session mode).
  let token = null;
  const authHeader = r.headers.get("Authorization") || r.headers.get("authorization");
  if (authHeader && authHeader.startsWith("Bearer ")) token = authHeader.slice(7);
  try {
    const d = await r.clone().json();
    if (!token) {
      for (const k of TOKEN_KEYS) if (d && d[k]) { token = d[k]; break; }
      // Может быть nested: { data: { token: ... }} или { result: { token: ... }}
      for (const wrap of ["data", "result", "payload"]) {
        if (!token && d && d[wrap]) {
          for (const k of TOKEN_KEYS) if (d[wrap][k]) { token = d[wrap][k]; break; }
        }
      }
    }
  } catch { /* не JSON — возможно session-cookie login */ }
  // Session-cookie mode: ответ 200 и браузер сам сохранил cookie.
  // Тогда считаем auth успешным без явного токена (будем ходить с credentials: include).
  return { ok: true, token, url, sessionMode: !token };
}

async function mrAuth() {
  const attempts = [];
  // Порядок: (url, json vs form, field). `username` первым — реальный
  // Merchrules API (prod) его требует по 422-error.
  for (const base of MR_BASES) {
    for (const path of AUTH_PATHS) {
      for (const mode of ["json", "form"]) {
        for (const field of ["username", "email", "login"]) {
          try {
            const res = await _tryLoginOnce(base, path, field, mode);
            if (res.ok) {
              MR_BASE = base;
              return { token: res.token, sessionMode: !!res.sessionMode, base };
            }
            attempts.push(`${base.split("//")[1]}${path}[${mode}/${field}]:HTTP ${res.status}${res.body ? ` — ${res.body.slice(0, 150)}` : ""}`);
            // Если 404/405 — путь точно не тот, нет смысла перебирать field'ы
            if (res.status === 404 || res.status === 405) break;
          } catch (e) {
            attempts.push(`${base}[${mode}/${field}]:${e.message}`);
          }
        }
      }
    }
  }
  const all4xx = attempts.every(a => /HTTP 40[0-9]|HTTP 4[12][0-9]/.test(a));
  if (all4xx) {
    throw new Error("Merchrules: неверный логин или пароль, либо API изменился. Последние ответы: " + attempts.slice(-3).join(" | "));
  }
  throw new Error("Merchrules auth failed (" + attempts.length + " попыток): " + attempts.slice(-3).join(" | "));
}

async function mrGet(authResult, path, params = {}) {
  const url = new URL(`${MR_BASE}${path}`);
  Object.entries(params).forEach(([k,v]) => url.searchParams.set(k, v));
  const headers = {};
  if (authResult && authResult.token) headers["Authorization"] = `Bearer ${authResult.token}`;
  const r = await fetch(url, {
    headers,
    // Если auth был session-cookie, здесь тоже credentials include
    credentials: authResult && authResult.sessionMode ? "include" : "same-origin",
  });
  if (!r.ok) return null;
  return r.json();
}

// Lightweight auth-only check for UI "Test connection" button
export async function testMrAuth() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) {
    throw new Error("Введите логин и пароль Merchrules");
  }
  const token = await mrAuth();
  // Verify token works by fetching one account
  const accData = await mrGet(token, "/backend-v2/accounts", { limit: 1 });
  if (accData === null) throw new Error("Merchrules: авторизация прошла, но API вернул ошибку");
  const accounts = accData?.accounts || accData?.items || (Array.isArray(accData) ? accData : []);
  return { ok: true, accounts_total: accounts.length };
}

export async function doSync() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) throw new Error("Merchrules: не заданы логин/пароль");
  if (!CONFIG.HUB_URL)  throw new Error("AM Hub URL не настроен");

  const token = await mrAuth();

  // Получаем аккаунты и сайты
  const [accData, siteData] = await Promise.all([
    mrGet(token, "/backend-v2/accounts", { limit: 500 }),
    mrGet(token, "/backend-v2/sites",    { limit: 500 }),
  ]);

  const accounts = accData?.accounts || accData?.items || (Array.isArray(accData) ? accData : []);
  const sites    = siteData?.sites    || siteData?.items || (Array.isArray(siteData) ? siteData : []);

  // Для каждого аккаунта собираем задачи и встречи
  const payload = [];
  const batchSize = 10;

  for (let i = 0; i < sites.length; i += batchSize) {
    const batch = sites.slice(i, i + batchSize);
    const enriched = await Promise.all(batch.map(async site => {
      const siteId = String(site.id || site.site_id || "");
      if (!siteId) return null;

      const [tasksData, meetingsData] = await Promise.all([
        mrGet(token, "/backend-v2/tasks", { site_id: siteId, status: "plan,in_progress,blocked", limit: 100 }),
        mrGet(token, "/backend-v2/meetings", { site_id: siteId, limit: 20 }),
      ]);

      const tasks    = tasksData?.tasks    || tasksData?.items    || [];
      const meetings = meetingsData?.meetings || meetingsData?.items || [];

      return {
        id:           siteId,
        name:         site.name || site.title || `Site ${siteId}`,
        segment:      site.segment || site.tariff || null,
        domain:       site.domain || site.url || null,
        health_score: site.health_score || site.healthScore || null,
        tasks: tasks.map(t => ({
          id:        String(t.id || ""),
          title:     t.title || t.name || "",
          status:    t.status || "plan",
          priority:  t.priority || "medium",
          due_date:  t.due_date || t.dueDate || null,
          team:      t.team || t.assignee || null,
          task_type: t.type || t.task_type || null,
        })),
        meetings: meetings.map(m => ({
          id:      String(m.id || ""),
          date:    m.date || m.meeting_date || m.createdAt || null,
          type:    m.type || m.meeting_type || "meeting",
          title:   m.title || m.name || null,
          summary: m.summary || m.description || null,
        })),
      };
    }));
    payload.push(...enriched.filter(Boolean));
    // Небольшая пауза между батчами
    if (i + batchSize < sites.length) {
      await new Promise(r => setTimeout(r, 300));
    }
  }

  const result = await syncAccounts(payload);
  return {
    ok: true,
    clients_synced: result.clients_synced || payload.length,
    tasks_synced:   result.tasks_synced   || 0,
  };
}
