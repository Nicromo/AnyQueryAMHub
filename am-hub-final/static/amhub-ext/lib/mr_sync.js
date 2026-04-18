/**
 * mr_sync.js — Merchrules синхронизация
 * Портировано из старого AM Hub Sync расширения.
 */

import { CONFIG } from "./config.js";
import { syncAccounts } from "./hub.js";

const MR_BASE = "https://merchrules.any-platform.ru";

async function mrAuth() {
  const attempts = [];
  for (const field of ["email", "login", "username"]) {
    try {
      const r = await fetch(`${MR_BASE}/backend-v2/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD }),
      });
      if (r.ok) {
        const d = await r.json();
        const token = d.token || d.access_token || d.accessToken;
        if (token) return token;
        attempts.push(`${field}:ok-but-no-token`);
      } else {
        // Try to get error body for diagnostics
        let body = "";
        try { body = (await r.text()).slice(0, 120); } catch {}
        attempts.push(`${field}:HTTP ${r.status}${body ? ` — ${body}` : ""}`);
      }
    } catch (e) {
      attempts.push(`${field}:${e.message}`);
    }
  }
  // Build informative error: if all returned 401/403 → credentials wrong; else → other issue
  const all401 = attempts.every(a => a.includes("HTTP 401") || a.includes("HTTP 403"));
  if (all401) {
    throw new Error("Merchrules: неверный логин или пароль");
  }
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
