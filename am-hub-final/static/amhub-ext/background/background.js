/**
 * AM Hub Extension — background service worker
 * Объединяет: Merchrules Sync + Search Quality Checkup + Token Capture
 */

import { CONFIG, loadConfig } from "../lib/config.js";
import { checkConnection, pushTokens } from "../lib/hub.js";
import { doSync } from "../lib/mr_sync.js";
import { fetchCabinet, fetchQueries, fetchMerchRules, submitCheckupResults } from "../lib/hub.js";
import { searchDiginetica } from "../lib/diginetica.js";
import { analyzeQuery } from "../lib/analyzer.js";
import { getAiRecommendations } from "../lib/ai.js";

// ── Constants ────────────────────────────────────────────────────────────────
const ICON_URL = "icons/icon128.png";
const NOTIF_SYNC_OK    = "amhub_sync_ok";
const NOTIF_SYNC_ERR   = "amhub_sync_err";
const NOTIF_TOKEN      = "amhub_token";
const NOTIF_AUTH_STALE = "amhub_auth_stale";

const BADGE_COLOR_ERR  = "#f0556a";
const BADGE_COLOR_WARN = "#f0b429";
const BADGE_COLOR_OK   = "#23d18b";

// ── Checkup state ─────────────────────────────────────────────────────────────
let checkup = {
  cabinetId: null, apiKey: null, products: {}, activeProduct: "sort",
  siteUrl: null, clientName: null, managerName: null,
  queries: [], queryType: "top", results: [], merchRules: [],
  mode: null, status: "idle", currentIndex: 0, selectorConfig: {},
};

// ── Sync state ────────────────────────────────────────────────────────────────
let syncState = { status: "idle", lastSync: null, error: null, lastResult: null };

// ── Heartbeat state (in-memory; restart OK) ──────────────────────────────────
let authFailCount = 0;
let tokenBadgeTimer = null;

// ── Init ──────────────────────────────────────────────────────────────────────
loadConfig().then(() => {
  chrome.storage.local.get(["selectorConfig", "managerName"], data => {
    if (data.selectorConfig) checkup.selectorConfig = data.selectorConfig;
    if (data.managerName)    checkup.managerName    = data.managerName;
  });
});

// ── Side Panel ────────────────────────────────────────────────────────────────
// Клик по иконке расширения открывает боковую панель, а не popup.
// Панель остаётся открытой при смене вкладок и при клике вне — как у Claude.
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.warn("[AM Hub] sidePanel setPanelBehavior:", err));
}

// ── Alarms ────────────────────────────────────────────────────────────────────
chrome.alarms.create("mr_sync", { periodInMinutes: 30 });
chrome.alarms.create("heartbeat", { periodInMinutes: 5 });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "mr_sync")   runMrSync(false);
  if (alarm.name === "heartbeat") runHeartbeat();
});

// ── Notifications: click opens side panel / popup ────────────────────────────
if (chrome.notifications && chrome.notifications.onClicked) {
  chrome.notifications.onClicked.addListener(async (notifId) => {
    try {
      // Side Panel нельзя открыть без активного жеста пользователя в некоторых
      // случаях; попытаемся открыть панель на текущей вкладке, иначе — popup окно.
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && chrome.sidePanel && chrome.sidePanel.open) {
        try { await chrome.sidePanel.open({ tabId: tab.id }); } catch {}
      }
    } catch (e) {
      console.warn("[AM Hub] notif click handler:", e);
    }
    try { chrome.notifications.clear(notifId); } catch {}
  });
}

// ── Message router ────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  const handlers = {
    // ── Hub ──────────────────────────────────────────────────────────────────
    RELOAD_CONFIG:    () => loadConfig().then(() => ({ ok: true })),
    CHECK_CONNECTION: () => checkConnection(),
    GET_FULL_STATE:   () => ({ checkup: { ...checkup }, sync: { ...syncState } }),

    // ── MR Sync ──────────────────────────────────────────────────────────────
    SYNC_NOW:     () => runMrSync(true, { full: msg.full === true }),
    GET_SYNC_STATUS: () => ({ ...syncState }),

    // ── Checkup ──────────────────────────────────────────────────────────────
    GET_CHECKUP_STATE:   () => ({ ...checkup }),
    SET_MANAGER_NAME:    () => { checkup.managerName = msg.name; return { ok: true }; },
    SET_ACTIVE_PRODUCT:  () => {
      checkup.activeProduct = msg.product;
      const p = checkup.products[msg.product];
      if (p) checkup.apiKey = p.apiKey;
      return { ok: true };
    },
    LOAD_CABINET:    () => handleLoadCabinet(msg.cabinetId),
    SET_API_KEY_MANUAL: () => {
      checkup.apiKey     = msg.apiKey;
      checkup.siteUrl    = msg.siteUrl    || null;
      checkup.clientName = msg.clientName || null;
      checkup.products   = { sort: { apiKey: msg.apiKey, url: "" } };
      checkup.activeProduct = "sort";
      return { ok: true };
    },
    SET_QUERIES:     () => { checkup.queries   = msg.queries;   return { ok: true }; },
    SET_QUERY_TYPE:  () => { checkup.queryType = msg.queryType; return { ok: true }; },
    LOAD_QUERIES:    () => handleLoadQueries(msg.cabinetId, msg.queryType),
    RUN_CALIBRATION: () => handleCalibration(msg.tabId),
    RUN_CHECK:       () => handleRunCheck(),
    OVERRIDE_SCORE:  () => {
      const r = checkup.results.find(r => r.query === msg.query);
      if (r) r.manualScore = msg.score;
      return { ok: true };
    },
    SET_SELECTOR: () => {
      if (checkup.cabinetId) {
        checkup.selectorConfig[checkup.cabinetId] = msg.selector;
        chrome.storage.local.set({ selectorConfig: checkup.selectorConfig });
      }
      return { ok: true };
    },
    SUBMIT_RESULTS: () => submitCheckupResults(
      checkup.cabinetId, checkup.results,
      { queryType: checkup.queryType, managerName: checkup.managerName, mode: checkup.mode, product: checkup.activeProduct }
    ),

    // ── Token capture (from content script) ─────────────────────────────────
    CAPTURE_TOKENS:  () => handleCaptureTokens(msg.system, msg.url, sender.tab?.id),
    TOKEN_CAPTURED:  () => handleTokenCaptured(msg.tokenType, msg.token, msg.ts),
  };

  const handler = handlers[msg.type];
  if (!handler) return;
  const result = handler();
  if (result instanceof Promise) { result.then(respond, (e) => respond({ ok: false, error: e?.message || String(e) })); return true; }
  respond(result);
  return true;
});

// ── Hashing helper (simple non-crypto hash для сравнения токенов) ────────────
function simpleHash(s) {
  if (!s) return "";
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h) + s.charCodeAt(i);
    h |= 0;
  }
  return String(h);
}

// ── Notifications helpers ────────────────────────────────────────────────────
function notify(id, title, message) {
  try {
    chrome.notifications.clear(id, () => {
      chrome.notifications.create(id, {
        type: "basic",
        iconUrl: ICON_URL,
        title,
        message: String(message || "").slice(0, 300),
      });
    });
  } catch (e) {
    console.warn("[AM Hub] notify error:", e);
  }
}

// ── Badge helpers ────────────────────────────────────────────────────────────
function setBadge(text, color) {
  try {
    chrome.action.setBadgeText({ text: text || "" });
    if (text && color) chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) { /* noop */ }
}
function clearBadge() { setBadge("", null); }

function flashTokenBadge() {
  setBadge("🔑", BADGE_COLOR_OK);
  if (tokenBadgeTimer) clearTimeout(tokenBadgeTimer);
  tokenBadgeTimer = setTimeout(() => {
    // снимаем только если сейчас всё ещё ключик
    chrome.action.getBadgeText({}, (t) => { if (t === "🔑") clearBadge(); });
  }, 5000);
}

// ── Merchrules Sync ───────────────────────────────────────────────────────────
async function runMrSync(manual = false, opts = {}) {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD || !CONFIG.HUB_URL) {
    syncState = { status: "error", error: "Не настроены MR или Hub", lastSync: null, lastResult: null };
    if (manual) {
      setBadge("!", BADGE_COLOR_ERR);
      notify(NOTIF_SYNC_ERR, "AM Hub — Ошибка Sync", syncState.error);
    }
    return { ok: false };
  }
  syncState.status = "running";
  try {
    // Прокидываем флаг полного resync'а, если doSync умеет его принимать.
    const result = (doSync.length >= 1)
      ? await doSync({ full: opts.full === true })
      : await doSync();
    const now = new Date().toLocaleString("ru-RU");
    syncState = { status: "ok", lastSync: now, lastResult: result, error: null };

    const clients = result?.clients_synced || 0;
    const tasks   = result?.tasks_synced   || 0;

    clearBadge();
    if (manual && (clients > 0 || tasks > 0)) {
      notify(
        NOTIF_SYNC_OK,
        "AM Hub — Sync",
        `✅ Синк завершён: ${clients} клиентов, ${tasks} задач`
      );
    }
    return { ok: true, result };
  } catch (e) {
    syncState = { status: "error", error: e.message, lastSync: syncState.lastSync, lastResult: null };
    setBadge("!", BADGE_COLOR_ERR);
    if (manual) {
      notify(NOTIF_SYNC_ERR, "AM Hub — Ошибка Sync", `❌ Ошибка синка: ${e.message}`);
    }
    return { ok: false, error: e.message };
  }
}

// ── Checkup ───────────────────────────────────────────────────────────────────
async function handleLoadCabinet(cabinetId) {
  checkup.cabinetId = cabinetId;
  try {
    const data = await fetchCabinet(cabinetId);
    checkup.apiKey     = data.apiKey;
    checkup.siteUrl    = data.siteUrl;
    checkup.clientName = data.clientName;
    checkup.products   = data.products || {};
    checkup.activeProduct = checkup.products.sort ? "sort" : (Object.keys(checkup.products)[0] || "sort");
    try { checkup.merchRules = await fetchMerchRules(cabinetId); } catch { checkup.merchRules = []; }
    return { ok: true, ...data, activeProduct: checkup.activeProduct,
             availableProducts: Object.keys(checkup.products), merchRulesCount: checkup.merchRules.length };
  } catch (e) { return { ok: false, error: e.message }; }
}

async function handleLoadQueries(cabinetId, queryType) {
  try {
    const queries = await fetchQueries(cabinetId, queryType);
    checkup.queries = queries; checkup.queryType = queryType;
    return { ok: true, queries };
  } catch (e) { return { ok: false, error: e.message }; }
}

async function handleCalibration(tabId) {
  if (!checkup.apiKey || !checkup.queries.length) return { ok: false, error: "Нет apiKey или запросов" };
  checkup.status = "calibrating";
  const pCfg    = checkup.products[checkup.activeProduct] || {};
  const apiKey  = pCfg.apiKey || checkup.apiKey;
  const searchUrl = pCfg.url || CONFIG.DIGINETICA_SEARCH_URL;
  const firstQ  = typeof checkup.queries[0] === "string" ? checkup.queries[0] : checkup.queries[0].query;
  try {
    const apiData = await searchDiginetica(firstQ, apiKey, searchUrl);
    const apiTop3 = (apiData.products || []).slice(0, 3).map(p => p.id);
    let siteProducts;
    try { siteProducts = await chrome.tabs.sendMessage(tabId, { type: "GET_PRODUCT_IDS", count: 3 }); }
    catch { siteProducts = { ids: [] }; }
    if (!siteProducts?.ids?.length) {
      const saved = checkup.selectorConfig[checkup.cabinetId];
      if (saved) { checkup.mode = "site"; return { ok: true, mode: "site", message: "Используем сохранённый селектор" }; }
      return { ok: true, mode: "unknown", needSelector: true, message: "Укажите CSS-селектор товаров" };
    }
    const siteTop3 = siteProducts.ids.slice(0, 3);
    const match = apiTop3.length >= 3 && siteTop3.length >= 3 &&
      apiTop3[0] === siteTop3[0] && apiTop3[1] === siteTop3[1] && apiTop3[2] === siteTop3[2];
    checkup.mode = match ? "api" : "site";
    return { ok: true, mode: checkup.mode, message: match ? "Выдача совпадает — API-режим" : "Сайт-режим", apiTop3, siteTop3 };
  } catch (e) { return { ok: false, error: e.message }; }
}

async function handleRunCheck() {
  if (!checkup.apiKey || !checkup.queries.length) return { ok: false, error: "Нет apiKey или запросов" };
  const pCfg     = checkup.products[checkup.activeProduct] || {};
  const apiKey   = pCfg.apiKey || checkup.apiKey;
  const searchUrl = pCfg.url || CONFIG.DIGINETICA_SEARCH_URL;
  checkup.status = "running"; checkup.results = []; checkup.currentIndex = 0;

  for (let i = 0; i < checkup.queries.length; i++) {
    checkup.currentIndex = i;
    const q   = typeof checkup.queries[i] === "string" ? checkup.queries[i] : checkup.queries[i].query;
    const imp = typeof checkup.queries[i] === "object" ? checkup.queries[i].impressions : null;
    try {
      const apiData  = await searchDiginetica(q, apiKey, searchUrl);
      const analysis = analyzeQuery(q, apiData, checkup.merchRules);
      let aiRec = null;
      if (CONFIG.AI_ENABLED && analysis.score <= CONFIG.AI_MAX_SCORE) {
        try { aiRec = await getAiRecommendations(q, analysis.score, analysis.details, analysis.flags, analysis.meta); }
        catch {}
      }
      checkup.results.push({
        index: i + 1, query: q, impressions: imp, product: checkup.activeProduct,
        total: analysis.meta.total, autoScore: analysis.score, manualScore: null,
        reason: analysis.reason, recommendation: analysis.recommendation,
        aiRecommendation: aiRec, details: analysis.details, flags: analysis.flags, meta: analysis.meta,
      });
    } catch (e) {
      checkup.results.push({ index: i+1, query: q, impressions: imp, product: checkup.activeProduct,
        total: 0, autoScore: 0, manualScore: null, reason: `Ошибка: ${e.message}`,
        recommendation: [], aiRecommendation: null, details: [], flags: [], meta: {} });
    }
    chrome.runtime.sendMessage({ type: "PROGRESS", current: i+1, total: checkup.queries.length }).catch(()=>{});
    if (i < checkup.queries.length - 1) await new Promise(r => setTimeout(r, CONFIG.REQUEST_DELAY_MS));
  }
  checkup.status = "done";
  // Автосохранение
  if (checkup.cabinetId) {
    submitCheckupResults(checkup.cabinetId, checkup.results, {
      queryType: checkup.queryType, managerName: checkup.managerName,
      mode: checkup.mode, product: checkup.activeProduct,
    }).catch(() => {});
  }
  return { ok: true, results: checkup.results };
}

// ── Token capture: legacy path (куки через background) ──────────────────────
async function handleCaptureTokens(system, url, tabId) {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return { ok: false, error: "Hub не настроен" };

  try {
    const cookies = await chrome.cookies.getAll({ url });
    let captured = null;      // { type, token }

    if (system === "tbank_time") {
      const mm = cookies.find(c => c.name === "MMAUTHTOKEN");
      if (mm) captured = { type: "tbank", token: mm.value };
    }
    if (system === "ktalk") {
      // KTalk — сначала пробуем localStorage/sessionStorage через scripting
      if (tabId) {
        try {
          const results = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => {
              const keys = ["access_token", "ktalk_token", "token"];
              for (const k of keys) {
                const v = localStorage.getItem(k);
                if (v) return { key: k, value: v };
              }
              for (const k of keys) {
                const v = sessionStorage.getItem(k);
                if (v) return { key: k, value: v };
              }
              return null;
            },
          });
          const found = results?.[0]?.result;
          if (found?.value) captured = { type: "ktalk", token: found.value };
        } catch {}
      }
      if (!captured) {
        const ktCookie = cookies.find(c =>
          c.name.toLowerCase().includes("token") || c.name.toLowerCase().includes("auth")
        );
        if (ktCookie) captured = { type: "ktalk", token: ktCookie.value };
      }
    }

    if (!captured) return { ok: false, error: "Токен не найден в cookies/storage" };

    const pushed = await pushTokenToHub(captured.type, captured.token);
    return { ok: pushed };
  } catch (e) {
    console.warn("[AM Hub] handleCaptureTokens error:", e);
    return { ok: false, error: e.message };
  }
}

// ── Token capture: новый путь — от content script уже приходит токен ─────────
async function handleTokenCaptured(tokenType, token, ts) {
  if (!tokenType || !token) return { ok: false, error: "empty token" };
  if (tokenType !== "ktalk" && tokenType !== "tbank") return { ok: false, error: "unknown type" };

  const pushed = await pushTokenToHub(tokenType, token, ts);
  return { ok: pushed };
}

/**
 * Сохраняет в storage + пушит в AM Hub с защитой от спама.
 * Возвращает true, если отправили (или считаем отправленным: нечего слать — токен не менялся).
 */
async function pushTokenToHub(type, token, ts) {
  if (!token) return false;

  // 1. Сохранить локально (полная копия)
  const storageKey = type === "ktalk" ? "last_ktalk_token" : "last_time_token";
  const hashKey    = type === "ktalk" ? "last_pushed_ktalk_hash" : "last_pushed_tbank_hash";
  const hash = simpleHash(token);

  try {
    await chrome.storage.local.set({ [storageKey]: token });
  } catch (e) {
    console.warn("[AM Hub] storage.set token:", e);
  }

  // 2. Получить предыдущий hash, сравнить
  let prevHash = "";
  try {
    const d = await chrome.storage.local.get([hashKey]);
    prevHash = d[hashKey] || "";
  } catch {}

  if (prevHash === hash) {
    // токен не поменялся с прошлого раза — молча пропускаем
    return true;
  }

  // 3. Проверить наличие настроек хаба
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) {
    console.log("[AM Hub] HUB не настроен — токен сохранён локально, но не отправлен");
    return false;
  }

  // 4. POST /api/auth/tokens/push
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/tokens/push`, {
      method: "POST",
      headers: {
        "Authorization": CONFIG.HUB_TOKEN,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ type, token, ts: ts || Date.now() }),
    });
    if (!r.ok) {
      console.warn(`[AM Hub] tokens/push HTTP ${r.status}`);
      return false;
    }

    // 5. запомнить hash успешной отправки
    try { await chrome.storage.local.set({ [hashKey]: hash }); } catch {}

    // 6. UX: нотификация + бейдж
    notify(NOTIF_TOKEN, "AM Hub", `🔑 Токен ${type} обновлён`);
    flashTokenBadge();
    return true;
  } catch (e) {
    console.warn("[AM Hub] pushTokenToHub fetch error:", e);
    return false;
  }
}

// ── Heartbeat: раз в 5 минут проверяем /api/auth/me ──────────────────────────
async function runHeartbeat() {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return;
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/me`, {
      headers: { "Authorization": CONFIG.HUB_TOKEN },
    });
    if (r.status === 401) {
      authFailCount += 1;
      if (authFailCount >= 3) {
        setBadge("⚠", BADGE_COLOR_WARN);
        notify(NOTIF_AUTH_STALE, "AM Hub", "Токен устарел, обнови в настройках");
      }
    } else if (r.ok) {
      if (authFailCount > 0) {
        authFailCount = 0;
        // снимаем предупреждающий бейдж, если он был поставлен хартбитом
        chrome.action.getBadgeText({}, (t) => { if (t === "⚠") clearBadge(); });
      }
    }
    // Прочие коды (5xx, сетевые ошибки) не трогают счётчик — это не про токен.
  } catch (e) {
    // сетевая ошибка — не считаем это "токен протух"
    console.log("[AM Hub] heartbeat network error:", e?.message);
  }
}

// Инициализация при установке
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("mr_sync",   { periodInMinutes: 30 });
  chrome.alarms.create("heartbeat", { periodInMinutes: 5 });
});
