/**
 * config.js — единые настройки AM Hub Extension
 *
 * Хранилище:
 *   - chrome.storage.sync  — синхронизируется через Google-аккаунт,
 *                            переживает uninstall/reinstall и миграцию на другой ПК.
 *   - chrome.storage.local — fallback если sync недоступен (guest-mode, enterprise).
 *
 * При сохранении пишем в оба. При чтении предпочитаем sync.
 */

const SYNC_KEYS = [
  "hub_url", "hub_token",
  "mr_login", "mr_password", "mr_site_ids",
  "groq_api_key", "cf_account_id", "cf_api_token",
  "manager_name",
];

export const CONFIG = {
  // ── AM Hub ──────────────────────────────────────────────────────
  HUB_URL:   "",
  HUB_TOKEN: "",

  // ── Merchrules Sync ─────────────────────────────────────────────
  MR_LOGIN:    "",
  MR_PASSWORD: "",
  MR_SITE_IDS: "",
  MR_SYNC_INTERVAL_MIN: 30,

  // ── Search Quality Checkup ──────────────────────────────────────
  DIGINETICA_SEARCH_URL: "https://sort.diginetica.net/search",
  GROQ_API_KEY: "",
  GROQ_MODEL:   "llama-3.3-70b-versatile",
  GROQ_URL:     "https://api.groq.com/openai/v1/chat/completions",
  CF_ACCOUNT_ID: "",
  CF_API_TOKEN:  "",
  CF_MODEL:      "@cf/meta/llama-3.1-8b-instruct",

  TOP_N_PRODUCTS:     10,
  SCORE_3_THRESHOLD:  0.8,
  SCORE_2_THRESHOLD:  0.4,
  REQUEST_DELAY_MS:   300,
  AI_ENABLED:         true,
  AI_MAX_SCORE:       2,
};

function _merge(data) {
  if (data.hub_url)       CONFIG.HUB_URL      = String(data.hub_url).trim().replace(/\/$/, "");
  if (data.hub_token)     CONFIG.HUB_TOKEN    = "Bearer " + String(data.hub_token).trim();
  if (data.mr_login)      CONFIG.MR_LOGIN     = data.mr_login;
  if (data.mr_password)   CONFIG.MR_PASSWORD  = data.mr_password;
  if (data.mr_site_ids)   CONFIG.MR_SITE_IDS  = data.mr_site_ids;
  if (data.groq_api_key)  CONFIG.GROQ_API_KEY = data.groq_api_key;
  if (data.cf_account_id) CONFIG.CF_ACCOUNT_ID = data.cf_account_id;
  if (data.cf_api_token)  CONFIG.CF_API_TOKEN  = data.cf_api_token;
}

export async function loadConfig() {
  // 1. sync storage (приоритет — переживает reinstall)
  try {
    if (chrome.storage.sync) {
      const syncData = await chrome.storage.sync.get(SYNC_KEYS);
      _merge(syncData);
    }
  } catch (_) {}
  // 2. local storage (перекрывает sync если локально свежее; также fallback)
  try {
    const localData = await chrome.storage.local.get(SYNC_KEYS);
    _merge(localData);
  } catch (_) {}
  return CONFIG;
}

/**
 * Сохраняет креды в оба хранилища — sync (для reinstall-persistence) и local
 * (для скорости / fallback).  Принимает объект { hub_url, hub_token, ... }.
 */
export async function saveCreds(obj) {
  const payload = {};
  for (const k of SYNC_KEYS) {
    if (Object.prototype.hasOwnProperty.call(obj, k)) payload[k] = obj[k];
  }
  try { await chrome.storage.local.set(payload); } catch (_) {}
  try { if (chrome.storage.sync) await chrome.storage.sync.set(payload); } catch (_) {}
  _merge(payload);
  return CONFIG;
}

/**
 * Чистит все сохранённые креды (sync + local). Нужно при logout / reset.
 */
export async function clearCreds() {
  try { await chrome.storage.local.remove(SYNC_KEYS); } catch (_) {}
  try { if (chrome.storage.sync) await chrome.storage.sync.remove(SYNC_KEYS); } catch (_) {}
}
