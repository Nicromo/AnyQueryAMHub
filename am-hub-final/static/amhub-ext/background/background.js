/**
 * AM Hub Extension — background service worker
 * Объединяет: Merchrules Sync + Search Quality Checkup + Token Capture
 */

import { CONFIG, loadConfig } from "../lib/config.js";
import { checkConnection, pushTokens } from "../lib/hub.js";
import { doSync, testMrAuth } from "../lib/mr_sync.js";
import { fetchCabinet, fetchQueries, fetchMerchRules, submitCheckupResults } from "../lib/hub.js";
import { searchDiginetica } from "../lib/diginetica.js";
import { analyzeQuery } from "../lib/analyzer.js";
import { getAiRecommendations } from "../lib/ai.js";

// ── Checkup state ─────────────────────────────────────────────────────────────
let checkup = {
  cabinetId: null, apiKey: null, products: {}, activeProduct: "sort",
  siteUrl: null, clientName: null, managerName: null,
  queries: [], queryType: "top", results: [], merchRules: [],
  mode: null, status: "idle", currentIndex: 0, selectorConfig: {},
};

// ── Sync state ────────────────────────────────────────────────────────────────
let syncState = { status: "idle", lastSync: null, error: null, lastResult: null };

// ── Init ──────────────────────────────────────────────────────────────────────
loadConfig().then(() => {
  chrome.storage.local.get(["selectorConfig", "managerName"], data => {
    if (data.selectorConfig) checkup.selectorConfig = data.selectorConfig;
    if (data.managerName)    checkup.managerName    = data.managerName;
  });
  // version check in background after 5s (moved here, removed duplicate below)
});

// ── Side Panel (Chrome 114+): клик по иконке открывает боковую панель ────────
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(err => console.warn("[AM Hub] sidePanel.setPanelBehavior:", err));
}

// ── Alarms ────────────────────────────────────────────────────────────────────
chrome.alarms.create("mr_sync",       { periodInMinutes: 30 });
chrome.alarms.create("version_check", { periodInMinutes: 360 }); // every 6h
chrome.alarms.create("heartbeat",     { periodInMinutes: 5 });   // проверка токена

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "mr_sync")       runMrSync(false);
  if (alarm.name === "version_check") checkForUpdate();
  if (alarm.name === "heartbeat")     runHeartbeat();
});

// ── Notifications + Badge helpers ─────────────────────────────────────────────
// chrome.notifications требует абсолютный URL к иконке (не относительный).
const ICON_URL = chrome.runtime.getURL("icons/icon128.png");
function amhNotify(id, title, message) {
  try {
    chrome.notifications.clear(id, () => {
      chrome.notifications.create(id, {
        type: "basic", iconUrl: ICON_URL,
        title, message: String(message || "").slice(0, 300),
      });
    });
  } catch (e) { /* noop */ }
}
function amhSetBadge(text, color) {
  try {
    chrome.action.setBadgeText({ text: text || "" });
    if (text && color) chrome.action.setBadgeBackgroundColor({ color });
  } catch (e) { /* noop */ }
}

// ── Action log: хранит последние 50 записей в chrome.storage.local ─────────
// Использование: amhLog("sync", "Merchrules sync запущен") / amhLog("ok"|"err", ...)
const AMH_LOG_KEY = "amhub_actions";
const AMH_LOG_MAX = 50;
async function amhLog(kind, text, extra) {
  const entry = {
    ts: Date.now(),
    kind: String(kind || "info"),
    text: String(text || "").slice(0, 240),
    extra: extra || null,
  };
  try {
    const data = await chrome.storage.local.get(AMH_LOG_KEY);
    const list = Array.isArray(data[AMH_LOG_KEY]) ? data[AMH_LOG_KEY] : [];
    list.unshift(entry);
    if (list.length > AMH_LOG_MAX) list.length = AMH_LOG_MAX;
    await chrome.storage.local.set({ [AMH_LOG_KEY]: list });
  } catch (_) { /* noop */ }
  // Нотификация popup'у если он открыт
  try { chrome.runtime.sendMessage({ type: "ACTION_LOG_APPEND", entry }); } catch (_) {}
}

// ── Heartbeat: раз в 5 мин пингуем /api/auth/me, 3 подряд 401 → алерт ────────
let _amhAuthFailCount = 0;
async function runHeartbeat() {
  // На холодном старте SW config ещё не загружен — без этого шлём Bearer '' → 401.
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) {
    await loadConfig();
  }
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return;
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/me`, {
      headers: { "Authorization": CONFIG.HUB_TOKEN }
    });
    if (r.status === 401) {
      _amhAuthFailCount++;
      if (_amhAuthFailCount >= 3) {
        amhSetBadge("⚠", "#f0b429");
        amhNotify("amhub_auth_stale", "⚠️ Токен AM Hub устарел",
          "Обнови токен в настройках расширения");
      }
    } else if (r.ok) {
      _amhAuthFailCount = 0;
      // Убираем ⚠ если висел — но не трогаем ! (ошибка синка)
      chrome.action.getBadgeText({}, txt => {
        if (txt === "⚠") amhSetBadge("", null);
      });
    }
  } catch (e) {
    // network errors — счётчик не трогаем
  }
}

// ── Token auto-push: получаем TOKEN_CAPTURED от content script → шлём в хаб ──
async function pushTokenToHub(tokenType, rawToken) {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN || !rawToken) return;
  // Дедуп по простому хэшу, чтобы не спамить при каждом fetch'е страницы
  const hashKey = `last_pushed_${tokenType}_hash`;
  let h = 0;
  for (let i = 0; i < rawToken.length; i++) { h = ((h << 5) - h) + rawToken.charCodeAt(i); h |= 0; }
  const sig = String(h);
  const prev = await chrome.storage.local.get(hashKey);
  if (prev[hashKey] === sig) return; // уже отправляли этот же токен

  // Backend схема /api/auth/tokens/push ожидает поля time_token / ktalk_token,
  // а не {type, token}. Маппим по tokenType (tbank_time/tbank/ktalk).
  const body = {};
  if (tokenType === "ktalk") body.ktalk_token = rawToken;
  else body.time_token = rawToken;
  try {
    const r = await fetch(`${CONFIG.HUB_URL}/api/auth/tokens/push`, {
      method: "POST",
      headers: { "Authorization": CONFIG.HUB_TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (r.ok) {
      await chrome.storage.local.set({ [hashKey]: sig });
      amhNotify("amhub_token", `🔑 Токен ${tokenType} обновлён`,
        "Токен автоматически отправлен в AM Hub");
      amhSetBadge("🔑", "#23d18b");
      amhLog("ok", `Токен ${tokenType} отправлен в AM Hub`);
      setTimeout(() => {
        chrome.action.getBadgeText({}, txt => {
          if (txt === "🔑") amhSetBadge("", null);
        });
      }, 5000);
    } else {
      console.warn("[AM Hub] pushTokenToHub: HTTP", r.status);
      amhLog("err", `Не удалось отправить ${tokenType}-токен: HTTP ${r.status}`);
    }
  } catch (e) {
    console.warn("[AM Hub] pushTokenToHub:", e);
    amhLog("err", `Ошибка отправки ${tokenType}-токена: ${e.message || e}`);
  }
}

// ── Notifications click → открыть side panel ─────────────────────────────────
if (chrome.notifications && chrome.notifications.onClicked) {
  chrome.notifications.onClicked.addListener(async (notifId) => {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab && chrome.sidePanel && chrome.sidePanel.open) {
        try { await chrome.sidePanel.open({ tabId: tab.id }); } catch {}
      }
    } catch {}
    try { chrome.notifications.clear(notifId); } catch {}
  });
}

// ── Auto-update: check hub for new version ────────────────────────────────────
const CURRENT_VERSION = chrome.runtime.getManifest().version;

async function checkForUpdate() {
  // Ensure CONFIG is populated — loadConfig may not have finished at startup timeout.
  if (!CONFIG.HUB_URL) {
    try { await loadConfig(); } catch {}
  }
  if (!CONFIG.HUB_URL) return;
  try {
    const resp = await fetch(`${CONFIG.HUB_URL}/api/extension/version`, {
      headers: CONFIG.HUB_TOKEN ? { "Authorization": CONFIG.HUB_TOKEN } : {}
    });
    if (!resp.ok) return;
    const info = await resp.json();

    const latest = info.version || "";
    if (!latest || latest === CURRENT_VERSION) return;

    // New version available — store info + show notification
    await chrome.storage.local.set({
      ext_update_available: true,
      ext_latest_version: latest,
      ext_update_url: info.download_url || `${CONFIG.HUB_URL}/settings/extension`,
      ext_changelog: info.changelog || "",
    });

    chrome.notifications.create("ext_update", {
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon48.png"),
      title: `AM Hub: версия ${latest} доступна`,
      message: info.changelog || "Нажмите чтобы обновить расширение",
      buttons: [{ title: "Обновить" }],
      requireInteraction: true,
    });
    amhLog("info", `Доступна новая версия ${latest} (текущая ${CURRENT_VERSION})`);
  } catch (e) { /* silently ignore */ }
}

chrome.notifications.onButtonClicked.addListener((notifId, btnIdx) => {
  if (notifId === "ext_update" && btnIdx === 0) {
    chrome.storage.local.get("ext_update_url", d => {
      chrome.tabs.create({ url: d.ext_update_url || `${CONFIG.HUB_URL}/settings/extension` });
    });
  }
});

// Check on startup (after config loads)
setTimeout(checkForUpdate, 5000);

// ── Message router ────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  const handlers = {
    // ── Wake-up ping (MV3 service worker keep-alive) ─────────────────────────
    PING:             () => ({ pong: true }),
    // ── Hub ──────────────────────────────────────────────────────────────────
    RELOAD_CONFIG:    () => loadConfig().then(() => {
      // Пользователь нажал 'Сохранить настройки' → поменял токен.
      // Сбрасываем счётчик 401, убираем бейдж ⚠ — даём новому токену шанс
      // без флэш-сообщения 'токен устарел'.
      _amhAuthFailCount = 0;
      chrome.action.getBadgeText({}, txt => {
        if (txt === "⚠" || txt === "🔑") amhSetBadge("", null);
      });
      // Немедленный heartbeat — чтобы моментально проверить новый токен
      runHeartbeat().catch(() => {});
      return { ok: true };
    }),
    CHECK_CONNECTION: async () => {
      // Гарантируем что CONFIG загружен из storage до запроса — иначе
      // на холодном старте SW token ещё пустой и хаб отвечает 401.
      await loadConfig();
      return checkConnection();
    },
    GET_FULL_STATE:   () => ({ checkup: { ...checkup }, sync: { ...syncState } }),

    // ── MR Sync ──────────────────────────────────────────────────────────────
    SYNC_NOW:     () => runMrSync(true),
    GET_SYNC_STATUS: () => ({ ...syncState }),
    TEST_MR_AUTH: async () => {
      try { return await testMrAuth(); }
      catch (e) { return { ok: false, error: e.message }; }
    },

    // ── Checkup ──────────────────────────────────────────────────────────────
    GET_CHECKUP_STATE:   () => ({ ...checkup }),
    SET_MANAGER_NAME:    () => { checkup.managerName = msg.name; return { ok: true }; },
    SET_ACTIVE_PRODUCT:  () => {
      checkup.activeProduct = msg.product;
      const p = checkup.products[msg.product];
      if (p) checkup.apiKey = p.apiKey;
      return { ok: true };
    },
    LOAD_CABINET:    () => handleLoadCabinet(msg.cabinetId, msg.directApiKey, msg.directSiteUrl),
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
    CAPTURE_TOKENS: () => handleCaptureTokens(msg.system, msg.url, sender.tab?.id),
    TOKEN_CAPTURED: async () => {
      // content script сообщает: «я поймал токен» → сохраняем + пушим в хаб
      const keyByType = { ktalk: "last_ktalk_token", tbank: "last_time_token" };
      const storeKey = keyByType[msg.tokenType];
      if (storeKey && msg.token) {
        await chrome.storage.local.set({ [storeKey]: msg.token });
        try { await pushTokenToHub(msg.tokenType, msg.token); }
        catch (e) { console.warn("[AM Hub] pushTokenToHub failed:", e); }
      }
      return { ok: true };
    },
  };

  const handler = handlers[msg.type];
  if (!handler) return;
  const result = handler();
  if (result instanceof Promise) { result.then(respond); return true; }
  respond(result);
  return true;
});

// ── Merchrules Sync ───────────────────────────────────────────────────────────
async function runMrSync(manual = false) {
  if (!CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD || !CONFIG.HUB_URL) {
    const msg = !CONFIG.HUB_URL ? "Не указан Hub URL в настройках"
              : !CONFIG.MR_LOGIN || !CONFIG.MR_PASSWORD ? "Не указан логин/пароль Merchrules"
              : "Настройки неполные";
    syncState = { status: "error", error: msg, lastSync: null, lastResult: null };
    amhLog("err", "Sync: " + msg);
    return { ok: false, error: msg };
  }
  syncState.status = "running";
  amhLog("sync", manual ? "Sync запущен вручную" : "Авто-sync (каждые 30 мин)");
  try {
    const result = await doSync();
    const now = new Date().toLocaleString("ru-RU");
    syncState = { status: "ok", lastSync: now, lastResult: result, error: null };
    amhSetBadge("", null);  // убираем ! при успехе
    amhLog("ok", `Sync OK: ${result.clients_synced || 0} клиентов, ${result.tasks_synced || 0} задач`);
    if (manual) {
      chrome.notifications.create({
        type: "basic", iconUrl: chrome.runtime.getURL("icons/icon48.png"),
        title: "AM Hub — Sync",
        message: `✅ ${result.clients_synced || 0} клиентов, ${result.tasks_synced || 0} задач`,
      });
    }
    return { ok: true, result };
  } catch (e) {
    syncState = { status: "error", error: e.message, lastSync: syncState.lastSync, lastResult: null };
    amhSetBadge("!", "#f0556a");  // ! при ошибке
    amhLog("err", "Sync error: " + (e.message || e).slice(0, 200));
    if (manual) {
      chrome.notifications.create({
        type: "basic", iconUrl: chrome.runtime.getURL("icons/icon48.png"),
        title: "AM Hub — Ошибка Sync", message: e.message.slice(0, 100),
      });
    }
    return { ok: false, error: e.message };
  }
}

// ── Checkup ───────────────────────────────────────────────────────────────────
async function handleLoadCabinet(cabinetId, directApiKey, directSiteUrl) {
  checkup.cabinetId = cabinetId || "direct";
  try {
    // Прямой API-ключ — пропускаем вызов AM Hub, сразу настраиваем state.
    if (directApiKey) {
      checkup.apiKey        = directApiKey;
      checkup.siteUrl       = directSiteUrl || "";
      checkup.clientName    = "Прямой API-ключ";
      checkup.products      = { sort: true };  // по умолчанию sort — остальное пусть включают калибровкой
      checkup.activeProduct = "sort";
      checkup.merchRules    = [];
      return {
        ok: true,
        clientName: checkup.clientName,
        apiKey: directApiKey,
        siteUrl: checkup.siteUrl,
        activeProduct: "sort",
        availableProducts: ["sort"],
        merchRulesCount: 0,
      };
    }
    // Обычный путь — через AM Hub
    if (!cabinetId) return { ok: false, error: "Нужен ID кабинета или API-ключ" };
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
  // Для режима «прямой API-ключ» (cabinetId == "direct") — пользователь
  // сам вбивает запросы в textarea. Отдаём пустой список — не падаем.
  if (!cabinetId || cabinetId === "direct") {
    checkup.queries = []; checkup.queryType = queryType;
    return { ok: true, queries: [], note: "Вбей запросы вручную в textarea ниже" };
  }
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

// ── Token capture ─────────────────────────────────────────────────────────────
async function handleCaptureTokens(system, url, tabId) {
  if (!CONFIG.HUB_URL || !CONFIG.HUB_TOKEN) return { ok: false, error: "Hub не настроен" };

  try {
    const cookies = await chrome.cookies.getAll({ url });
    const tokens = {};

    if (system === "tbank_time") {
      const mm = cookies.find(c => c.name === "MMAUTHTOKEN");
      if (mm) tokens.time_token = mm.value;
    }
    if (system === "ktalk") {
      // KTalk использует localStorage для access_token — достаём через scripting
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
              // Пробуем sessionStorage
              for (const k of keys) {
                const v = sessionStorage.getItem(k);
                if (v) return { key: k, value: v };
              }
              return null;
            },
          });
          const found = results?.[0]?.result;
          if (found?.value) tokens.ktalk_token = found.value;
        } catch {}
      }
      // Также проверяем cookies KTalk
      const ktCookies = cookies.filter(c => c.name.toLowerCase().includes("token") || c.name.toLowerCase().includes("auth"));
      if (ktCookies.length) tokens.ktalk_cookie = ktCookies[0].value;
    }

    if (!Object.keys(tokens).length) {
      amhLog("err", `Токен ${system} не найден в cookies/storage`);
      return { ok: false, error: "Токен не найден в cookies/storage" };
    }

    const result = await pushTokens(tokens);
    if (result.ok) {
      amhLog("ok", `${system} токен захвачен`);
      // Запоминаем в storage.local — popup renders status по этим ключам.
      // Сохраняем значение (а не raw token, чтобы дедуп-хэш pushTokenToHub работал).
      const storePatch = {};
      if (tokens.time_token) storePatch.last_time_token = tokens.time_token;
      if (tokens.ktalk_token) storePatch.last_ktalk_token = tokens.ktalk_token;
      if (Object.keys(storePatch).length) {
        try { await chrome.storage.local.set(storePatch); } catch (_) {}
      }
    } else {
      amhLog("err", `${system} токен не ушёл в хаб`);
    }
    return { ok: result.ok || false };
  } catch (e) {
    amhLog("err", `Capture ${system} error: ${e.message || e}`);
    return { ok: false, error: e.message };
  }
}

// Инициализация при установке
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("mr_sync", { periodInMinutes: 30 });
});
