/**
 * diginetica.js — Клиент Diginetica Search API
 */

import { CONFIG } from "./config.js";

/**
 * Поиск через Diginetica API
 * @param {string} query — поисковый запрос
 * @param {string} apiKey — ключ клиента
 * @returns {Object} — полный ответ API
 */
export async function searchDiginetica(query, apiKey, searchUrl) {
  const params = new URLSearchParams({
    st: query,
    apiKey,
    strategy: "advanced_xname,zero_queries",
    withSku: "false",
    fullData: "true",
    withCorrection: "true",
    withFacets: "false",
    treeFacets: "false",
    regionId: "global",
    useCategoryPrediction: "false",
    size: "20",
    offset: "0",
    showUnavailable: "false",
    unavailableMultiplier: "0.0002",
    preview: "false",
    sort: "DEFAULT",
    searchConfiguration: "false",
  });

  const resp = await fetch(`${searchUrl || CONFIG.DIGINETICA_SEARCH_URL}?${params}`);
  if (!resp.ok) throw new Error(`Diginetica API: ${resp.status}`);
  return resp.json();
}
