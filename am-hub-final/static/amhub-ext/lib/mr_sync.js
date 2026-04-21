/**
 * mr_sync.js — Merchrules синхронизация с реальными аналитическими данными
 * Endpoints: /backend-v2/auth/login, /backend-v2/sites, /backend-v2/tasks,
 *            /backend-v2/meetings, /api/site/all, /api/report/agg, /api/report/daily,
 *            /backend-v2/analytics/top_queries, null_queries, zero_queries
 */

import { CONFIG } from "./config.js";
import { syncAccounts } from "./hub.js";

const MR_BASE = "https://merchrules.any-platform.ru";

// Все агрегированные метрики одним запросом
const AGG_METRICS = [
  "SESSIONS_TOTAL,ORDERS_TOTAL,REVENUE_TOTAL,CONVERSION,RPS,AOV",
  "AUTOCOMPLETE_SESSIONS_TOTAL,AUTOCOMPLETE_ORDERS_TOTAL,AUTOCOMPLETE_SESSION_REVENUE,AUTOCOMPLETE_SESSIONS_CONVERSION",
  "AUTOCOMPLETE_CTR,AUTOCOMPLETE_CLICKS",
  "AUTOCOMPLETE_QUERY_BLOCK_CLICK,AUTOCOMPLETE_PRODUCT_BLOCK_CLICK,AUTOCOMPLETE_CATEGORY_BLOCK_CLICK",
  "AUTOCOMPLETE_HISTORY_BLOCK_CLICK,AUTOCOMPLETE_TAP_BLOCK_CLICK,AUTOCOMPLETE_BRAND_BLOCK_CLICK",
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_TOTAL,AUTOCOMPLETE_AND_SEARCH_SESSIONS_ORDERS_TOTAL",
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_REVENUE,AUTOCOMPLETE_AND_SEARCH_SESSIONS_CONVERSION",
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_RPS,AUTOCOMPLETE_AND_SEARCH_SESSIONS_AOV",
  "SEARCH_EVENTS_TOTAL,ZERO_QUERIES_COUNT,CORRECTION_TOTAL",
  "CORRECTION_SESSION_ORDERS,CORRECTION_REVENUE,CORRECTION_CONVERSION",
].join(",");

const DAILY_METRICS = [
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_TOTAL,SESSIONS_TOTAL",
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_ORDERS_TOTAL,ORDERS_TOTAL",
  "AUTOCOMPLETE_AND_SEARCH_SESSIONS_REVENUE,REVENUE_TOTAL",
].join(",");

// ── Дата-хелпер ───────────────────────────────────────────────────────────────

function getDateRange(days = 30) {
  const to = new Date();
  const from = new Date(to);
  from.setDate(from.getDate() - days);

  const fmtDate = d => d.toISOString().slice(0, 10);
  const toDate  = fmtDate(to);
  const fromDate = fmtDate(from);

  // Для /api/report/* используем ISO с временем
  const fromISO = fromDate + "T00:00:00";
  const toISO   = toDate   + "T23:59:59";

  return { from: fromDate, to: toDate, fromISO, toISO };
}

// ── Auth ──────────────────────────────────────────────────────────────────────

async function mrAuth() {
  for (const field of ["email", "login", "username"]) {
    try {
      const r = await fetch(`${MR_BASE}/backend-v2/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD }),
      });
      if (r.ok) {
        const d = await r.json();
        const token = d.token || d.access_token || d.accessToken;
        if (token) return token;
      }
    } catch {}
  }
  throw new Error("Merchrules: авторизация не удалась — проверьте логин/пароль");
}

// ── Универсальный GET ─────────────────────────────────────────────────────────

async function mrGet(token, path, params = {}) {
  const url = new URL(`${MR_BASE}${path}`);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  try {
    const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) return null;
    return r.json();
  } catch { return null; }
}

// ── API-ключи сайтов из /api/site/all ────────────────────────────────────────

async function mrGetSiteApiKeys(token) {
  try {
    const data = await mrGet(token, "/api/site/all");
    if (!data) return {};
    const sites = data.sites || data.items || (Array.isArray(data) ? data : []);
    const map = {};
    for (const s of sites) {
      const id  = String(s.id || s.site_id || s.siteId || "");
      const key = s.apiKey || s.api_key || s.token || null;
      if (id && key) map[id] = key;
    }
    return map;
  } catch { return {}; }
}

// ── Аналитика одного сайта (последние 30 дней) ────────────────────────────────

async function mrGetAnalytics(token, siteId) {
  const { from, to, fromISO, toISO } = getDateRange(30);
  const h = { Authorization: `Bearer ${token}` };

  // Все 5 запросов параллельно, ошибки не блокируют
  const [aggR, dailyR, topR, nullR, zeroR] = await Promise.allSettled([
    fetch(
      `${MR_BASE}/api/report/agg/${siteId}/global?name=${AGG_METRICS}&from=${fromISO}&to=${toISO}&siteId=${siteId}`,
      { headers: h }
    ).then(r => r.ok ? r.json() : null).catch(() => null),

    fetch(
      `${MR_BASE}/api/report/daily/${siteId}/global?name=${DAILY_METRICS}&from=${fromISO}&to=${toISO}&siteId=${siteId}`,
      { headers: h }
    ).then(r => r.ok ? r.json() : null).catch(() => null),

    fetch(
      `${MR_BASE}/backend-v2/analytics/top_queries?site_id=${siteId}&date_from=${from}&date_to=${to}&platform=all&limit=120`,
      { headers: h }
    ).then(r => r.ok ? r.json() : null).catch(() => null),

    fetch(
      `${MR_BASE}/backend-v2/analytics/null_queries?site_id=${siteId}&date_from=${from}&date_to=${to}&platform=all&limit=90`,
      { headers: h }
    ).then(r => r.ok ? r.json() : null).catch(() => null),

    fetch(
      `${MR_BASE}/backend-v2/analytics/zero_queries?site_id=${siteId}&date_from=${from}&date_to=${to}&platform=all&limit=90&mode=aggregated`,
      { headers: h }
    ).then(r => r.ok ? r.json() : null).catch(() => null),
  ]);

  return {
    period:      { from, to },
    agg:         aggR.value   ?? null,
    daily:       dailyR.value ?? null,
    top_queries: topR.value   ?? null,
    null_queries: nullR.value ?? null,
    zero_queries: zeroR.value ?? null,
    collected_at: new Date().toISOString(),
  };
}

// ── Основной sync ─────────────────────────────────────────────────────────────

export async function doSync() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) throw new Error("Merchrules: не заданы логин/пароль");
  if (!CONFIG.HUB_URL)  throw new Error("AM Hub URL не настроен");

  const token = await mrAuth();

  // Параллельно: аккаунты, сайты, API-ключи
  const [accData, siteData, apiKeys] = await Promise.all([
    mrGet(token, "/backend-v2/accounts", { limit: 500 }),
    mrGet(token, "/backend-v2/sites",    { limit: 500 }),
    mrGetSiteApiKeys(token),
  ]);

  // Объединяем — сначала сайты, потом аккаунты как fallback
  let items =
    siteData?.sites    || siteData?.items    || (Array.isArray(siteData) ? siteData : null) ||
    accData?.accounts  || accData?.items     || (Array.isArray(accData)  ? accData  : []);

  if (!items.length) throw new Error("Нет сайтов/аккаунтов в Merchrules — проверьте права доступа");

  const payload = [];
  const BATCH = 4; // не более 4 сайтов параллельно (5 запросов × 4 = 20 одновременных fetch)

  for (let i = 0; i < items.length; i += BATCH) {
    const batch = items.slice(i, i + BATCH);

    const enriched = await Promise.all(batch.map(async site => {
      const siteId = String(site.id || site.site_id || site.siteId || "");
      if (!siteId) return null;

      const [tasksData, meetingsData, analytics] = await Promise.all([
        mrGet(token, "/backend-v2/tasks",    { site_id: siteId, status: "plan,in_progress,blocked", limit: 100 }),
        mrGet(token, "/backend-v2/meetings", { site_id: siteId, limit: 20 }),
        mrGetAnalytics(token, siteId),
      ]);

      const tasks    = tasksData?.tasks    || tasksData?.items    || (Array.isArray(tasksData)    ? tasksData    : []);
      const meetings = meetingsData?.meetings || meetingsData?.items || (Array.isArray(meetingsData) ? meetingsData : []);

      return {
        id:           siteId,
        name:         site.name || site.title || `Site ${siteId}`,
        segment:      site.segment || site.tariff || null,
        domain:       site.domain  || site.url    || null,
        health_score: site.health_score || site.healthScore || null,
        api_key:      apiKeys[siteId] || null,  // Diginetica API-ключ → для чекапа
        analytics,

        tasks: tasks.map(t => ({
          id:        String(t.id || ""),
          title:     t.title || t.name || "",
          status:    t.status   || "plan",
          priority:  t.priority || "medium",
          due_date:  t.due_date || t.dueDate   || null,
          team:      t.team     || t.assignee  || null,
          task_type: t.type     || t.task_type || null,
        })),

        meetings: meetings.map(m => ({
          id:      String(m.id || ""),
          date:    m.date || m.meeting_date || m.createdAt || null,
          type:    m.type || m.meeting_type  || "meeting",
          title:   m.title   || m.name        || null,
          summary: m.summary || m.description || null,
        })),
      };
    }));

    payload.push(...enriched.filter(Boolean));

    // Пауза между батчами чтобы не перегружать API
    if (i + BATCH < items.length) {
      await new Promise(r => setTimeout(r, 400));
    }
  }

  const result = await syncAccounts(payload);
  return {
    ok: true,
    clients_synced: result.clients_synced || payload.length,
    tasks_synced:   result.tasks_synced   || 0,
    analytics_collected: payload.filter(p => p.analytics?.agg).length,
  };
}
