/**
 * mr_sync.js — тонкий клиент к backend.
 *
 * Раньше здесь был весь auth/sync с Merchrules прямо из расширения —
 * это создавало кучу проблем: CORS, cross-origin cookies, гадание
 * endpoint'ов, содержимое ответа, таймауты. Вся эта логика уже живёт
 * на сервере (merchrules_sync.py:get_auth_token — тот же код, что
 * scheduler использует для авто-синка).
 *
 * Теперь расширение просто делает POST к AM Hub с Bearer amh_* токеном,
 * а сервер сам ходит в Merchrules. Результат: один запрос вместо 100+.
 */

import { CONFIG } from "./config.js";

function _hubHeaders() {
  return {
    "Authorization": CONFIG.HUB_TOKEN,  // уже "Bearer amh_..." из config.js
    "Content-Type": "application/json",
    "Accept": "application/json",
  };
}

// Проверяем connection — быстро. Сервер сам ходит в Merchrules.
export async function testMrAuth() {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) {
    throw new Error("Введите логин и пароль Merchrules");
  }
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) {
    throw new Error("Сначала настрой URL хаба и токен");
  }
  const r = await fetch(`${CONFIG.HUB_URL}/api/integrations/test/merchrules`, {
    method: "POST",
    headers: _hubHeaders(),
    body: JSON.stringify({ login: CONFIG.MR_LOGIN, password: CONFIG.MR_PASSWORD }),
  });
  if (r.status === 401) throw new Error("Неверный токен AM Hub — обнови в настройках");
  if (!r.ok) throw new Error(`AM Hub: HTTP ${r.status}`);
  const d = await r.json();
  if (d.ok) {
    return { ok: true, message: d.message || "Подключено" };
  }
  throw new Error(d.error || "Merchrules отказал в авторизации");
}

// Запуск полного синка — сервер делает auth + выкачку + сохранение.
export async function doSync() {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) {
    throw new Error("AM Hub URL/токен не настроены");
  }
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD) {
    throw new Error("Merchrules логин/пароль не настроены");
  }
  // Site IDs: из CONFIG (строка через запятую) → массив.
  const siteIds = (CONFIG.MR_SITE_IDS || "")
    .split(/[,;\s]+/)
    .map(s => s.trim())
    .filter(Boolean);
  const r = await fetch(`${CONFIG.HUB_URL}/api/sync/merchrules`, {
    method: "POST",
    headers: _hubHeaders(),
    body: JSON.stringify({
      login: CONFIG.MR_LOGIN,
      password: CONFIG.MR_PASSWORD,
      site_ids: siteIds,
    }),
  });
  if (r.status === 401) throw new Error("Неверный токен AM Hub — обнови в настройках");
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(`AM Hub sync: HTTP ${r.status}${txt ? " — " + txt.slice(0, 200) : ""}`);
  }
  const d = await r.json();
  if (d.error) throw new Error(d.error);

  // После Merchrules — также синкаем Airtable (клиенты → по CSM email
  // раскидываются по менеджерам). Не критично если упал — основной
  // результат от Merchrules уже есть.
  let atRes = null;
  try {
    const ar = await fetch(`${CONFIG.HUB_URL}/api/sync/airtable`, {
      method: "POST", headers: _hubHeaders(), body: "{}",
    });
    if (ar.ok) atRes = await ar.json().catch(() => null);
  } catch (e) { /* non-fatal */ }

  return {
    clients_synced: (d.clients_synced || 0) + (atRes && atRes.clients_synced ? atRes.clients_synced : 0),
    tasks_synced: d.tasks_synced || 0,
    message: d.message || "Синхронизация выполнена",
    airtable: atRes && !atRes.error ? (atRes.message || `Airtable: ${atRes.clients_synced || 0} клиентов`) : null,
  };
}
