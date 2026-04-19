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
    headers = { "Content-Type": "application/json", "Accept": "application/json" };
    body = JSON.stringify({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD });
  }
  const r = await fetch(url, { method: "POST", headers, body, credentials: "include" });
  if (!r.ok) {
    let bodyText = "";
    try { bodyText = (await r.text()).slice(0, 400); } catch {}
    return { ok: false, status: r.status, body: bodyText, url };
  }
  // Проверяем Content-Type — если HTML, это landing page/SPA fallback, а не API.
  // Считаем такой "200" неудачей: реальный auth endpoint вернёт JSON или 401/422.
  const ct = (r.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("text/html")) {
    let bodyText = "";
    try { bodyText = (await r.text()).slice(0, 200); } catch {}
    return { ok: false, status: 200, body: `[HTML-response, не API] ${bodyText.replace(/\s+/g, " ")}`, url };
  }
  // Token может прийти в JSON, в Authorization, или как Set-Cookie (session mode).
  let token = null;
  const authHeader = r.headers.get("Authorization") || r.headers.get("authorization");
  if (authHeader && authHeader.startsWith("Bearer ")) token = authHeader.slice(7);
  let jsonBody = null;
  let jsonKeys = [];
  try {
    jsonBody = await r.clone().json();
    if (jsonBody && typeof jsonBody === "object") jsonKeys = Object.keys(jsonBody);
    if (!token && jsonBody) {
      for (const k of TOKEN_KEYS) if (jsonBody[k]) { token = jsonBody[k]; break; }
      // Nested: {data: {token}} / {result: {...}} / {payload: {...}}
      for (const wrap of ["data", "result", "payload"]) {
        if (!token && jsonBody[wrap] && typeof jsonBody[wrap] === "object") {
          for (const k of TOKEN_KEYS) if (jsonBody[wrap][k]) { token = jsonBody[wrap][k]; break; }
        }
      }
      // Последний шанс: любое string-поле длиннее 40 символов, начинающееся на eyJ (JWT),
      // чтобы хотя бы самому-маркированные JWT-токены ловить.
      if (!token) {
        for (const k of Object.keys(jsonBody)) {
          const v = jsonBody[k];
          if (typeof v === "string" && v.length > 40 && v.startsWith("eyJ")) { token = v; break; }
        }
      }
    }
  } catch { /* не JSON — возможно session-cookie login */ }
  // Куки из Set-Cookie браузер сохранит сам (см. credentials: include выше).
  // Если токен не нашёлся — считаем auth успешным в session-cookie mode.
  const hasSetCookie = !!r.headers.get("set-cookie");
  return {
    ok: true, token, url, sessionMode: !token,
    diagKeys: !token ? jsonKeys : null,  // для диагностики: что было в response body
    hasSetCookie,
  };
}

async function mrAuth() {
  const attempts = [];
  let firstSessionMode = null;  // первый успешный auth без токена — для диагностики

  for (const base of MR_BASES) {
    for (const path of AUTH_PATHS) {
      for (const mode of ["json", "form"]) {
        for (const field of ["username", "email", "login"]) {
          try {
            const res = await _tryLoginOnce(base, path, field, mode);
            if (res.ok && res.token) {
              // Нашли явный токен → успех
              MR_BASE = base;
              return { token: res.token, sessionMode: false, base };
            }
            if (res.ok && !res.token) {
              // 200 без токена — session-cookie mode, запомним на случай если
              // никакой другой попытки с явным токеном не будет
              if (!firstSessionMode) firstSessionMode = { base, url: res.url, diagKeys: res.diagKeys, hasSetCookie: res.hasSetCookie };
              // Продолжаем поиск — вдруг другая комбинация вернёт явный токен
              continue;
            }
            // HTTP-ошибка
            attempts.push(`${base.split("//")[1]}${path}[${mode}/${field}]:HTTP ${res.status}${res.body ? ` — ${res.body.slice(0, 150)}` : ""}`);
            if (res.status === 404 || res.status === 405) break;
          } catch (e) {
            attempts.push(`${base}[${mode}/${field}]:${e.message}`);
          }
        }
      }
    }
  }

  // Если была хоть одна успешная auth без явного токена — fallback на session-cookie.
  if (firstSessionMode) {
    MR_BASE = firstSessionMode.base;
    return { token: null, sessionMode: true, base: firstSessionMode.base };
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
  const headers = { "Accept": "application/json" };
  if (authResult && authResult.token) headers["Authorization"] = `Bearer ${authResult.token}`;
  const r = await fetch(url, { headers, credentials: "include" });
  if (!r.ok) return { _err: true, status: r.status, body: await r.text().catch(() => "") };
  // Content-Type защита: HTML однозначно не API — не парсим.
  const ct = (r.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("text/html")) {
    const bodyText = await r.text().catch(() => "");
    return { _err: true, status: r.status, body: `[HTML response, не API] ${bodyText.slice(0, 200).replace(/\s+/g, " ")}` };
  }
  // Для всего остального пробуем JSON с catch — некоторые API не ставят
  // Content-Type, но тело валидный JSON.
  try {
    const text = await r.text();
    return JSON.parse(text);
  } catch (e) {
    return { _err: true, status: r.status, body: `[JSON-parse failed: ${e.message}]` };
  }
}

// Пробует несколько возможных путей к API-ресурсу.
async function mrGetAny(authResult, paths, params = {}) {
  const tries = [];
  for (const p of paths) {
    const res = await mrGet(authResult, p, params);
    if (!res || !res._err) return { data: res, path: p };
    tries.push(`${p}:HTTP ${res.status}${res.body ? ` — ${res.body.slice(0, 120)}` : ""}`);
    // 404 — путь точно не тот; 401 — token/session не подходят, дальше тоже
    // нет смысла (одинаковый результат).
    if (res.status === 401 || res.status === 403) break;
  }
  return { data: null, path: null, tries };
}

// Lightweight auth-only check for UI "Test connection" button
export async function testMrAuth() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) {
    throw new Error("Введите логин и пароль Merchrules");
  }
  const auth = await mrAuth();
  const authLabel = auth.sessionMode ? "session-cookie" : "Bearer-token";
  // Verify auth works by fetching one account — пробуем несколько путей
  const r = await mrGetAny(auth, [
    "/backend-v2/accounts", "/backend/accounts", "/api/accounts", "/accounts",
  ], { limit: 1 });
  if (!r.data) {
    const tried = (r.tries || []).join(" | ");
    throw new Error(
      `Merchrules: auth=${authLabel} прошла, но API вернул ошибку. ` +
      `Пробовал пути: ${tried || "—"}. Проверь права учётки или напиши это сообщение админу.`
    );
  }
  const accData = r.data;
  const accounts = accData?.accounts || accData?.items || (Array.isArray(accData) ? accData : []);
  return { ok: true, accounts_total: accounts.length, path: r.path, authMode: authLabel };
}

export async function doSync() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) throw new Error("Merchrules: не заданы логин/пароль");
  if (!CONFIG.HUB_URL)  throw new Error("AM Hub URL не настроен");

  const token = await mrAuth();

  // Получаем аккаунты и сайты. mrGet теперь возвращает {_err,status,body}
  // при HTTP-ошибке — нужен explicit guard чтобы не принять ошибку за пустой ответ.
  const [accDataRaw, siteDataRaw] = await Promise.all([
    mrGet(token, "/backend-v2/accounts", { limit: 500 }),
    mrGet(token, "/backend-v2/sites",    { limit: 500 }),
  ]);
  if (accDataRaw && accDataRaw._err) {
    throw new Error(`Merchrules /accounts: HTTP ${accDataRaw.status}${accDataRaw.body ? " — " + accDataRaw.body.slice(0, 200) : ""}`);
  }
  if (siteDataRaw && siteDataRaw._err) {
    throw new Error(`Merchrules /sites: HTTP ${siteDataRaw.status}${siteDataRaw.body ? " — " + siteDataRaw.body.slice(0, 200) : ""}`);
  }
  const accData = accDataRaw;
  const siteData = siteDataRaw;
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

      const [tasksRaw, meetingsRaw] = await Promise.all([
        mrGet(token, "/backend-v2/tasks", { site_id: siteId, status: "plan,in_progress,blocked", limit: 100 }),
        mrGet(token, "/backend-v2/meetings", { site_id: siteId, limit: 20 }),
      ]);
      // Игнорируем _err на уровне отдельного сайта — просто пустой результат
      const tasksData = tasksRaw && tasksRaw._err ? null : tasksRaw;
      const meetingsData = meetingsRaw && meetingsRaw._err ? null : meetingsRaw;

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
