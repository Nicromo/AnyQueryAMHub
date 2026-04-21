/**
 * diginetica.js — Клиент Diginetica Search API
 */

import { CONFIG } from "./config.js";

/**
 * Поиск через Diginetica API
 * @param {string} query — поисковый запрос
 * @param {string} apiKey — ключ клиента
 * @param {string} url — (опц.) кастомный URL endpoint (иначе CONFIG.DIGINETICA_SEARCH_URL)
 * @returns {Object} — полный ответ API
 */
export async function searchDiginetica(query, apiKey, url) {
  const q = (query || "").trim();
  if (!q) throw new Error("Diginetica: пустой запрос");
  if (!apiKey) throw new Error("Diginetica: apiKey не задан");

  // Если пришёл URL со сформированными query-параметрами — подставляем st и apiKey,
  // не ломая уже существующие. Иначе строим полный набор параметров.
  const endpoint = (url || CONFIG.DIGINETICA_SEARCH_URL || "https://sort.diginetica.net/search").trim();
  let finalUrl;
  try {
    const u = new URL(endpoint);
    // Если в URL уже были query-параметры (debug-URL пользователя) — подменяем
    // только st + apiKey, остальное оставляем как есть.
    u.searchParams.set("st", q);
    u.searchParams.set("apiKey", apiKey);
    // Минимально-обязательные параметры, если их нет в URL
    const defaults = {
      strategy: "advanced_xname,zero_queries",
      withSku: "false", fullData: "true", withCorrection: "true",
      withFacets: "false", treeFacets: "false",
      regionId: "global", useCategoryPrediction: "false",
      size: "20", offset: "0",
      showUnavailable: "false", unavailableMultiplier: "0.0002",
      preview: "false", sort: "DEFAULT", searchConfiguration: "false",
    };
    for (const [k, v] of Object.entries(defaults)) {
      if (!u.searchParams.has(k)) u.searchParams.set(k, v);
    }
    finalUrl = u.toString();
  } catch {
    // URL невалидный — собираем своими руками
    const params = new URLSearchParams({
      st: q, apiKey,
      strategy: "advanced_xname,zero_queries",
      withSku: "false", fullData: "true", withCorrection: "true",
      withFacets: "false", treeFacets: "false",
      regionId: "global", useCategoryPrediction: "false",
      size: "20", offset: "0",
      showUnavailable: "false", unavailableMultiplier: "0.0002",
      preview: "false", sort: "DEFAULT", searchConfiguration: "false",
    });
    finalUrl = `${endpoint}?${params}`;
  }

  const resp = await fetch(finalUrl);
  if (!resp.ok) {
    let body = "";
    try { body = (await resp.text()).slice(0, 200); } catch {}
    throw new Error(`Diginetica API: ${resp.status}${body ? " · " + body : ""}`);
  }
  return resp.json();
}
