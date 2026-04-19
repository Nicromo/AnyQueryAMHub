/**
 * config.js — единые настройки AM Hub Extension
 * Все параметры читаются из chrome.storage при старте.
 */

export const CONFIG = {
  // ── AM Hub ──────────────────────────────────────────────────────
  HUB_URL:   "",   // из storage: hub_url
  HUB_TOKEN: "",   // из storage: hub_token  (Bearer ...)

  // ── Merchrules Sync ─────────────────────────────────────────────
  MR_LOGIN:    "",  // из storage: mr_login
  MR_PASSWORD: "",  // из storage: mr_password
  MR_SYNC_INTERVAL_MIN: 30,

  // ── Search Quality Checkup ──────────────────────────────────────
  DIGINETICA_SEARCH_URL: "https://sort.diginetica.net/search",
  GROQ_API_KEY: "",   // из storage: groq_api_key
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

export async function loadConfig() {
  return new Promise(resolve => {
    chrome.storage.local.get(
      ["hub_url","hub_token","mr_login","mr_password",
       "groq_api_key","cf_account_id","cf_api_token"],
      data => {
        if (data.hub_url)      CONFIG.HUB_URL   = data.hub_url.trim().replace(/\/$/, "");
        if (data.hub_token)    CONFIG.HUB_TOKEN = "Bearer " + String(data.hub_token).trim();
        if (data.mr_login)     CONFIG.MR_LOGIN    = data.mr_login;
        if (data.mr_password)  CONFIG.MR_PASSWORD = data.mr_password;
        if (data.groq_api_key) CONFIG.GROQ_API_KEY = data.groq_api_key;
        if (data.cf_account_id) CONFIG.CF_ACCOUNT_ID = data.cf_account_id;
        if (data.cf_api_token)  CONFIG.CF_API_TOKEN  = data.cf_api_token;
        resolve(CONFIG);
      }
    );
  });
}
