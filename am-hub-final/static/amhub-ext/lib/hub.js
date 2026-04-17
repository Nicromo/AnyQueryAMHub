/**
 * hub.js — клиент AM Hub API
 * Все запросы к хабу через этот модуль.
 */

import { CONFIG } from "./config.js";

function headers(extra = {}) {
  return { "Authorization": CONFIG.HUB_TOKEN, "Content-Type": "application/json", ...extra };
}

function check(resp, context) {
  if (resp.status === 401) throw new Error("Неверный токен AM Hub — обновите в настройках");
  if (!resp.ok) throw new Error(`${context}: HTTP ${resp.status}`);
  return resp.json();
}

// ── Sync ─────────────────────────────────────────────────────────────────────
export async function syncAccounts(accounts) {
  const r = await fetch(`${CONFIG.HUB_URL}/api/sync/extension`, {
    method: "POST", headers: headers(),
    body: JSON.stringify({ accounts }),
  });
  return check(r, "sync/extension");
}

// ── Checkup ───────────────────────────────────────────────────────────────────
export async function fetchCabinet(cabinetId) {
  const r = await fetch(`${CONFIG.HUB_URL}/api/cabinets/${cabinetId}`, { headers: headers() });
  return check(r, "cabinets");
}

export async function fetchQueries(cabinetId, queryType) {
  const r = await fetch(`${CONFIG.HUB_URL}/api/checkup/${cabinetId}/queries?type=${queryType}`, { headers: headers() });
  const d = await check(r, "checkup/queries");
  return d.queries || [];
}

export async function fetchMerchRules(cabinetId) {
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/cabinets/${cabinetId}/merch-rules`, { headers: headers() });
    return r.ok ? r.json() : [];
  } catch { return []; }
}

export async function submitCheckupResults(cabinetId, results, meta = {}) {
  if (!CONFIG.HUB_URL) return { ok: false, error: "HUB_URL не настроен" };
  const r = await fetch(`${CONFIG.HUB_URL}/api/checkup/${cabinetId}/results`, {
    method: "POST", headers: headers(),
    body: JSON.stringify({ results, ...meta }),
  });
  return check(r, "checkup/results");
}

// ── Token push ────────────────────────────────────────────────────────────────
export async function pushTokens(tokens) {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return { ok: false };
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/tokens/push`, {
      method: "POST", headers: headers(),
      body: JSON.stringify(tokens),
    });
    return r.ok ? r.json() : { ok: false };
  } catch { return { ok: false }; }
}

// ── Status check ──────────────────────────────────────────────────────────────
export async function checkConnection() {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return { ok: false, error: "Не настроен" };
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/me`, { headers: headers() });
    if (r.ok) { const d = await r.json(); return { ok: true, user: d }; }
    return { ok: false, error: `HTTP ${r.status}` };
  } catch (e) { return { ok: false, error: e.message }; }
}
