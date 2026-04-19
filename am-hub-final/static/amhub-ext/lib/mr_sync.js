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
let MR_BASE = MR_BASES[0];     // обновляется на тот, что сработал
let API_PREFIX = "/backend-v2"; // может быть "/api" или "/api/v1" — определяется в _verifyAuth

// Куда принимать токен из ответа (может называться по-разному у разных бэков).
const TOKEN_KEYS = ["token", "access_token", "accessToken", "jwt", "authToken", "auth_token", "sessionId", "session_id"];

// Возможные пути auth-эндпоинта. Пробуем от наиболее "правильного"
// для Spring Boot API (/api/*) к более старым variantам.
// Реальный GET возвращает 401 на /api/accounts — значит API под /api/,
// поэтому auth-endpoint тоже скорее всего /api/auth/login или /api/login.
const AUTH_PATHS = [
  "/api/auth/login",
  "/api/login",
  "/api/v1/auth/login",
  "/api/v2/auth/login",
  "/api/auth/signin",
  "/backend-v2/auth/login",
  "/backend/auth/login",
  "/auth/login",
  "/login",
];

// Путь для проверки что auth действительно установил сессию.
// Spring Boot с Security обычно 401 если не auth'ed — это и нужно.
const VERIFY_PATHS = [
  "/api/accounts",
  "/api/v1/accounts",
  "/api/auth/me",
  "/api/me",
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

// Быстрая проверка что auth действительно установил сессию.
// Возвращает {ok, prefix} где prefix — тот из которого начинался путь
// (например "/api" из "/api/accounts"). Этот prefix потом используется
// для всех последующих data-запросов (sync).
async function _verifyAuth(base, authResult) {
  for (const path of VERIFY_PATHS) {
    try {
      const headers = { "Accept": "application/json" };
      if (authResult && authResult.token) headers["Authorization"] = `Bearer ${authResult.token}`;
      const r = await fetch(`${base}${path}?limit=1`, { headers, credentials: "include", redirect: "manual" });
      if (r.type === "opaqueredirect" || r.status === 0) continue;  // редирект на login = не auth
      if (!r.ok) continue;
      const ct = (r.headers.get("content-type") || "").toLowerCase();
      if (ct.includes("text/html")) continue;
      // Вытаскиваем prefix — всё до последнего "/X" в path.
      // "/api/accounts" → "/api", "/api/v1/accounts" → "/api/v1", "/api/me" → "/api"
      const idx = path.lastIndexOf("/");
      const prefix = idx > 0 ? path.slice(0, idx) : "";
      return { ok: true, verifyPath: path, prefix };
    } catch { /* network error — пробуем следующий */ }
  }
  return { ok: false };
}

async function mrAuth() {
  const attempts = [];

  for (const base of MR_BASES) {
    for (const path of AUTH_PATHS) {
      for (const mode of ["json", "form"]) {
        for (const field of ["username", "email", "login"]) {
          try {
            const res = await _tryLoginOnce(base, path, field, mode);
            if (res.ok && res.token) {
              // Нашли явный токен — проверим что он реально работает на VERIFY_PATH
              const verified = await _verifyAuth(base, { token: res.token });
              if (verified.ok) {
                MR_BASE = base;
                API_PREFIX = verified.prefix;
                return { token: res.token, sessionMode: false, base, prefix: verified.prefix };
              }
              attempts.push(`${base.split("//")[1]}${path}[${mode}/${field}]:token-not-verified`);
              continue;
            }
            if (res.ok && !res.token) {
              // 200 без токена — session-cookie. Проверим что cookie реально
              // авторизует следующий запрос (иначе "успех" фальшивый — HTML
              // login-страницы тоже возвращают 200).
              const verified = await _verifyAuth(base, {});
              if (verified.ok) {
                MR_BASE = base;
                API_PREFIX = verified.prefix;
                return { token: null, sessionMode: true, base, prefix: verified.prefix };
              }
              attempts.push(`${base.split("//")[1]}${path}[${mode}/${field}]:session-cookie-not-verified`);
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

  // Все попытки исчерпаны — ни одна verify-auth не прошла.
  const all4xx = attempts.every(a => /HTTP 40[0-9]|HTTP 4[12][0-9]/.test(a));
  if (all4xx) {
    throw new Error("Merchrules: неверный логин или пароль, либо API изменился. Последние ответы: " + attempts.slice(-3).join(" | "));
  }
  const verifyIssues = attempts.filter(a => /not-verified/.test(a)).slice(-2);
  if (verifyIssues.length) {
    throw new Error(
      "Merchrules: login проходит, но API требует auth которого у нас нет " +
      "(устаревший способ или права учётки). Последние: " + verifyIssues.join(" | ")
    );
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
  // Content-Type, но тело валидный JSON. 204 No Content тоже ok.
  if (r.status === 204) return {};
  try {
    const text = await r.text();
    if (!text) return {};
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
  // После mrAuth() API_PREFIX уже знаем. Используем его.
  const r = await mrGet(auth, `${API_PREFIX}/accounts`, { limit: 1 });
  if (r && r._err) {
    throw new Error(
      `Merchrules ${API_PREFIX}/accounts: auth=${authLabel} прошла, HTTP ${r.status}${r.body ? " — " + r.body.slice(0, 200) : ""}`
    );
  }
  const accounts = r?.accounts || r?.items || (Array.isArray(r) ? r : []);
  return { ok: true, accounts_total: accounts.length, prefix: API_PREFIX, authMode: authLabel };
}

export async function doSync() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) throw new Error("Merchrules: не заданы логин/пароль");
  if (!CONFIG.HUB_URL)  throw new Error("AM Hub URL не настроен");

  const token = await mrAuth();

  // Получаем аккаунты и сайты. mrGet теперь возвращает {_err,status,body}
  // при HTTP-ошибке — нужен explicit guard чтобы не принять ошибку за пустой ответ.
  const [accDataRaw, siteDataRaw] = await Promise.all([
    mrGet(token, `${API_PREFIX}/accounts`, { limit: 500 }),
    mrGet(token, `${API_PREFIX}/sites`,    { limit: 500 }),
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
        mrGet(token, `${API_PREFIX}/tasks`, { site_id: siteId, status: "plan,in_progress,blocked", limit: 100 }),
        mrGet(token, `${API_PREFIX}/meetings`, { site_id: siteId, limit: 20 }),
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
