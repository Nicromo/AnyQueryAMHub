/**
 * mr_sync.js — Merchrules синхронизация
 * Портировано из старого AM Hub Sync расширения.
 */

import { CONFIG } from "./config.js";
import { syncAccounts } from "./hub.js";

const MR_BASE = "https://merchrules.any-platform.ru";

// Recursively find a token-looking string in an object (depth-limited)
function _findToken(obj, depth = 0) {
  if (!obj || typeof obj !== "object" || depth > 3) return null;
  const tokenKeys = ["token", "access_token", "accessToken", "jwt", "auth_token",
                     "authToken", "id_token", "idToken", "sessionToken",
                     "session_token", "bearer", "apiToken", "api_token"];
  for (const k of tokenKeys) {
    if (typeof obj[k] === "string" && obj[k].length > 10) return obj[k];
  }
  // Look inside nested objects
  for (const k of Object.keys(obj)) {
    const nested = _findToken(obj[k], depth + 1);
    if (nested) return nested;
  }
  return null;
}

// Extract token from a successful response (body object, string, or headers)
async function _extractToken(response) {
  // 1. Check response headers first (some APIs return token in Authorization/X-Auth-Token)
  const headerToken = response.headers.get("Authorization") ||
                      response.headers.get("X-Auth-Token") ||
                      response.headers.get("X-Access-Token");
  if (headerToken) {
    const stripped = headerToken.replace(/^Bearer\s+/i, "").trim();
    if (stripped.length > 10) return stripped;
  }

  // 2. Parse body — could be object, array, or raw string
  const text = await response.text();
  if (!text) return null;

  // 2a. Raw JWT string (starts with "eyJ")
  const trimmed = text.trim().replace(/^"/, "").replace(/"$/, "");
  if (/^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(trimmed)) {
    return trimmed;
  }

  // 2b. JSON object/array
  try {
    const d = JSON.parse(text);
    return _findToken(d);
  } catch {
    return null;
  }
}

async function _tryAuth(field, body, contentType) {
  return fetch(`${MR_BASE}/backend-v2/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": contentType },
    body,
  });
}

async function mrAuth() {
  const attempts = [];

  // Strategy 1: JSON with each of 3 username field names
  for (const field of ["username", "email", "login"]) {
    try {
      const r = await _tryAuth(
        field,
        JSON.stringify({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD }),
        "application/json"
      );
      if (r.ok) {
        const token = await _extractToken(r.clone());
        if (token) return token;
        // Couldn't find token in body/headers — log response for diagnosis
        let bodyKeys = "";
        try {
          const d = await r.json();
          if (d && typeof d === "object") bodyKeys = "keys: [" + Object.keys(d).slice(0, 10).join(",") + "]";
          else bodyKeys = "body-type: " + typeof d;
        } catch { bodyKeys = "non-json body"; }
        attempts.push(`json/${field}:200 no-token (${bodyKeys})`);
      } else {
        let body = "";
        try { body = (await r.text()).slice(0, 120); } catch {}
        attempts.push(`json/${field}:HTTP ${r.status}${body ? ` — ${body}` : ""}`);
      }
    } catch (e) {
      attempts.push(`json/${field}:${e.message}`);
    }
  }

  // Strategy 2: form-urlencoded OAuth2 password flow (FastAPI standard)
  try {
    const form = new URLSearchParams({ username: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD });
    const r = await _tryAuth("form", form.toString(), "application/x-www-form-urlencoded");
    if (r.ok) {
      const token = await _extractToken(r.clone());
      if (token) return token;
      attempts.push("form:200 no-token");
    } else {
      attempts.push(`form:HTTP ${r.status}`);
    }
  } catch (e) {
    attempts.push(`form:${e.message}`);
  }

  const all401 = attempts.every(a => a.includes("HTTP 401") || a.includes("HTTP 403"));
  if (all401) throw new Error("Merchrules: неверный логин или пароль");
  throw new Error("Merchrules auth failed: " + attempts.join(" | "));
}

async function mrGet(token, path, params = {}) {
  const url = new URL(`${MR_BASE}${path}`);
  Object.entries(params).forEach(([k,v]) => url.searchParams.set(k, v));
  const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
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
