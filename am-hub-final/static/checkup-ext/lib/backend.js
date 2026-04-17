/**
 * backend.js — Клиент AM Hub API для расширения Search Quality Checkup
 */

import { CONFIG } from "./config.js";

function authHeaders() {
  return {
    Authorization: CONFIG.BACKEND_TOKEN,
    "Content-Type": "application/json",
  };
}

/**
 * Получить данные кабинета (клиента) по ID
 * AM Hub: GET /api/cabinets/:id
 * Возвращает: { apiKey, siteUrl, clientName }
 */
export async function fetchCabinet(cabinetId) {
  if (!CONFIG.BACKEND_URL) throw new Error("AM Hub URL не настроен — откройте настройки расширения");
  const resp = await fetch(`${CONFIG.BACKEND_URL}/api/cabinets/${cabinetId}`, {
    headers: authHeaders(),
  });
  if (resp.status === 401) throw new Error("Неверный токен AM Hub — обновите в настройках расширения");
  if (resp.status === 404) throw new Error(`Кабинет ${cabinetId} не найден в AM Hub`);
  if (!resp.ok) throw new Error(`AM Hub error ${resp.status}`);
  const data = await resp.json();
  return {
    apiKey: data.apiKey || "",
    siteUrl: data.siteUrl || "",
    clientName: data.clientName || data.name || `Клиент ${cabinetId}`,
  };
}

/**
 * Получить список запросов для чекапа
 * AM Hub: GET /api/checkup/:cabinetId/queries?type=top|random|zero|zeroquery
 */
export async function fetchQueries(cabinetId, queryType) {
  if (!CONFIG.BACKEND_URL) throw new Error("AM Hub URL не настроен");
  const resp = await fetch(
    `${CONFIG.BACKEND_URL}/api/checkup/${cabinetId}/queries?type=${queryType}`,
    { headers: authHeaders() }
  );
  if (!resp.ok) throw new Error(`Ошибка загрузки запросов (${resp.status})`);
  const data = await resp.json();
  return data.queries || [];
}

/**
 * Получить мерч-правила клиента
 * AM Hub: GET /api/cabinets/:cabinetId/merch-rules
 */
export async function fetchMerchRules(cabinetId) {
  if (!CONFIG.BACKEND_URL) return [];
  try {
    const resp = await fetch(
      `${CONFIG.BACKEND_URL}/api/cabinets/${cabinetId}/merch-rules`,
      { headers: authHeaders() }
    );
    if (!resp.ok) return [];
    return await resp.json();
  } catch (e) {
    return [];
  }
}

/**
 * Отправить результаты чекапа в AM Hub
 * AM Hub: POST /api/checkup/:cabinetId/results
 */
export async function submitResults(cabinetId, results, meta = {}) {
  if (!CONFIG.BACKEND_URL) return { ok: false, error: "AM Hub URL не настроен" };
  try {
    const resp = await fetch(`${CONFIG.BACKEND_URL}/api/checkup/${cabinetId}/results`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        results,
        queryType: meta.queryType || "top",
        managerName: meta.managerName || "",
        mode: meta.mode || null,
      }),
    });
    if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
    return await resp.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}
