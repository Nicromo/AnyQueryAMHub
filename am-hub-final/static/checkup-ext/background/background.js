/**
 * Background Service Worker — Search Quality Checkup v2 + AM Hub
 * Поддерживает несколько продуктов Diginetica: Sort, Autocomplete, Recommendations
 */

import { CONFIG, loadConfigFromStorage } from "../lib/config.js";
import { fetchCabinet, fetchQueries, fetchMerchRules, submitResults } from "../lib/backend.js";
import { searchDiginetica } from "../lib/diginetica.js";
import { analyzeQuery } from "../lib/analyzer.js";
import { getAiRecommendations } from "../lib/ai.js";

let state = {
  cabinetId: null,
  apiKey: null,       // Основной ключ (Sort или первый доступный)
  products: {},       // { sort: {apiKey, url}, autocomplete: {apiKey, url}, recommendations: {apiKey, url} }
  activeProduct: "sort", // Активный продукт для чекапа
  siteUrl: null,
  clientName: null,
  managerName: null,
  queries: [],
  queryType: "top",
  results: [],
  merchRules: [],
  mode: null,
  status: "idle",
  currentIndex: 0,
  selectorConfig: {},
};

loadConfigFromStorage().then(() => {
  chrome.storage.local.get(["selectorConfig", "managerName"], (data) => {
    if (data.selectorConfig) state.selectorConfig = data.selectorConfig;
    if (data.managerName) state.managerName = data.managerName;
  });
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const handlers = {
    GET_STATE:           () => ({ ...state }),
    SET_MANAGER_NAME:    () => { state.managerName = msg.name; return { ok: true }; },
    RELOAD_CONFIG:       () => loadConfigFromStorage().then(() => ({ ok: true })),
    SET_ACTIVE_PRODUCT:  () => {
      state.activeProduct = msg.product;
      // Обновляем apiKey под активный продукт
      const p = state.products[msg.product];
      if (p) state.apiKey = p.apiKey;
      return { ok: true };
    },

    LOAD_CABINET: () => handleLoadCabinet(msg.cabinetId),
    SET_API_KEY_MANUAL: () => {
      state.apiKey     = msg.apiKey;
      state.siteUrl    = msg.siteUrl    || null;
      state.clientName = msg.clientName || null;
      // При ручном вводе создаём один продукт sort
      state.products   = { sort: { apiKey: msg.apiKey, url: "" } };
      state.activeProduct = "sort";
      return { ok: true };
    },

    SET_QUERIES:    () => { state.queries   = msg.queries;   return { ok: true }; },
    SET_QUERY_TYPE: () => { state.queryType = msg.queryType; return { ok: true }; },
    LOAD_QUERIES:   () => handleLoadQueries(msg.cabinetId, msg.queryType),

    RUN_CALIBRATION: () => handleCalibration(msg.tabId),
    RUN_CHECK:       () => handleRunCheck(),

    SET_SELECTOR: () => {
      if (state.cabinetId) {
        state.selectorConfig[state.cabinetId] = msg.selector;
        chrome.storage.local.set({ selectorConfig: state.selectorConfig });
      }
      return { ok: true };
    },
    OVERRIDE_SCORE: () => {
      const r = state.results.find(r => r.query === msg.query);
      if (r) r.manualScore = msg.score;
      return { ok: true };
    },
    SUBMIT_RESULTS: () => handleSubmitResults(),
  };

  const handler = handlers[msg.type];
  if (!handler) return;
  const result = handler();
  if (result instanceof Promise) { result.then(sendResponse); return true; }
  sendResponse(result);
  return true;
});

async function handleLoadCabinet(cabinetId) {
  state.cabinetId = cabinetId;
  try {
    const data = await fetchCabinet(cabinetId);
    state.apiKey     = data.apiKey;
    state.siteUrl    = data.siteUrl;
    state.clientName = data.clientName;
    state.products   = data.products || {};

    // Выбираем активный продукт: sort → первый доступный
    if (state.products.sort) {
      state.activeProduct = "sort";
    } else {
      const first = Object.keys(state.products)[0];
      state.activeProduct = first || "sort";
    }

    try { state.merchRules = await fetchMerchRules(cabinetId); } catch { state.merchRules = []; }

    return {
      ok: true,
      ...data,
      activeProduct: state.activeProduct,
      availableProducts: Object.keys(state.products),
      merchRulesCount: state.merchRules.length,
    };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function handleLoadQueries(cabinetId, queryType) {
  try {
    const queries = await fetchQueries(cabinetId, queryType);
    state.queries   = queries;
    state.queryType = queryType;
    return { ok: true, queries };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function handleCalibration(tabId) {
  if (!state.apiKey || !state.queries.length) return { ok: false, error: "Нет apiKey или запросов" };
  state.status = "calibrating";
  const firstQuery = typeof state.queries[0] === "string" ? state.queries[0] : state.queries[0].query;

  // URL для активного продукта
  const productCfg = state.products[state.activeProduct] || {};
  const searchUrl  = productCfg.url || CONFIG.DIGINETICA_SEARCH_URL;
  const apiKey     = productCfg.apiKey || state.apiKey;

  try {
    const apiData = await searchDiginetica(firstQuery, apiKey, searchUrl);
    const apiTop3 = (apiData.products || []).slice(0, 3).map(p => p.id);
    let siteProducts;
    try { siteProducts = await chrome.tabs.sendMessage(tabId, { type: "GET_PRODUCT_IDS", count: 3 }); }
    catch { siteProducts = { ids: [], method: "none" }; }

    if (!siteProducts?.ids?.length) {
      const saved = state.selectorConfig[state.cabinetId];
      if (saved) { state.mode = "site"; return { ok: true, mode: "site", message: "Используем сохранённый селектор" }; }
      return { ok: true, mode: "unknown", needSelector: true, message: "Укажите CSS-селектор товаров" };
    }

    const siteTop3 = siteProducts.ids.slice(0, 3);
    const match = apiTop3.length >= 3 && siteTop3.length >= 3 &&
      apiTop3[0] === siteTop3[0] && apiTop3[1] === siteTop3[1] && apiTop3[2] === siteTop3[2];
    state.mode = match ? "api" : "site";
    return { ok: true, mode: state.mode, message: match ? "Выдача совпадает — API-режим" : "Выдача расходится — сайт-режим", apiTop3, siteTop3 };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function handleRunCheck() {
  if (!state.apiKey || !state.queries.length) return { ok: false, error: "Нет apiKey или запросов" };

  const productCfg = state.products[state.activeProduct] || {};
  const searchUrl  = productCfg.url || CONFIG.DIGINETICA_SEARCH_URL;
  const apiKey     = productCfg.apiKey || state.apiKey;

  state.status = "running";
  state.results = [];
  state.currentIndex = 0;

  for (let i = 0; i < state.queries.length; i++) {
    state.currentIndex = i;
    const q = typeof state.queries[i] === "string" ? state.queries[i] : state.queries[i].query;
    const impressions = typeof state.queries[i] === "object" ? state.queries[i].impressions : null;
    try {
      const apiData = await searchDiginetica(q, apiKey, searchUrl);
      const analysis = analyzeQuery(q, apiData, state.merchRules);
      let aiRec = null;
      if (CONFIG.AI_ENABLED && analysis.score <= CONFIG.AI_MAX_SCORE) {
        try { aiRec = await getAiRecommendations(q, analysis.score, analysis.details, analysis.flags, analysis.meta); }
        catch (e) { console.warn("AI failed:", e.message); }
      }
      state.results.push({
        index: i + 1, query: q, impressions, product: state.activeProduct,
        total: analysis.meta.total, autoScore: analysis.score, manualScore: null,
        reason: analysis.reason, recommendation: analysis.recommendation,
        aiRecommendation: aiRec, details: analysis.details, flags: analysis.flags, meta: analysis.meta,
      });
    } catch (e) {
      state.results.push({
        index: i + 1, query: q, impressions, product: state.activeProduct,
        total: 0, autoScore: 0, manualScore: null,
        reason: `Ошибка: ${e.message}`, recommendation: [], aiRecommendation: null,
        details: [], flags: [], meta: {},
      });
    }
    chrome.runtime.sendMessage({ type: "PROGRESS", current: i + 1, total: state.queries.length }).catch(() => {});
    if (i < state.queries.length - 1) await new Promise(r => setTimeout(r, CONFIG.REQUEST_DELAY_MS));
  }

  state.status = "done";

  // Автосохранение в AM Hub
  if (state.cabinetId) {
    submitResults(state.cabinetId, state.results, {
      queryType: state.queryType, managerName: state.managerName,
      mode: state.mode, product: state.activeProduct,
    }).then(res => {
      if (res.ok) console.log("✅ Checkup saved:", res);
      else console.warn("⚠️ Save failed:", res.error);
    });
  }

  return { ok: true, results: state.results };
}

async function handleSubmitResults() {
  if (!state.cabinetId || !state.results.length) return { ok: false, error: "Нет результатов" };
  return submitResults(state.cabinetId, state.results, {
    queryType: state.queryType, managerName: state.managerName,
    mode: state.mode, product: state.activeProduct,
  });
}
