/**
 * config.js — настройки для Search Quality Checkup v2
 * Подключён к AM Hub — заполняется автоматически из настроек расширения.
 *
 * Для изменения AM Hub URL/токена используйте popup расширения,
 * либо обновите значения ниже вручную.
 */

export const CONFIG = {

  // ── AM Hub — единая база кабинетов ──────────────────────────────────────
  // Заполняется автоматически из chrome.storage при установке расширения.
  // Можно переопределить вручную для dev-окружения.

  BACKEND_URL: "",          // автоматически из storage: amhub_url
  BACKEND_TOKEN: "",        // автоматически из storage: amhub_token

  // Эти поля совпадают с BACKEND_URL — AM Hub отдаёт и кабинеты, и запросы
  DASHBOARD_QUERIES_URL: "",    // = BACKEND_URL
  DASHBOARD_MERCH_RULES_URL: "", // = BACKEND_URL


  // ── Diginetica Search API ───────────────────────────────────────────────
  DIGINETICA_SEARCH_URL: "https://sort.diginetica.net/search",


  // ── ИИ-рекомендации (Groq primary → Cloudflare fallback) ───────────────
  // Groq: бесплатный ключ на https://console.groq.com/keys
  GROQ_API_KEY: "",         // автоматически из storage: groq_api_key
  GROQ_MODEL: "llama-3.3-70b-versatile",
  GROQ_URL: "https://api.groq.com/openai/v1/chat/completions",

  // Cloudflare fallback: https://dash.cloudflare.com/
  CF_ACCOUNT_ID: "",
  CF_API_TOKEN: "",
  CF_MODEL: "@cf/meta/llama-3.1-8b-instruct",


  // ── Настройки проверки ──────────────────────────────────────────────────
  TOP_N_PRODUCTS: 10,
  SCORE_3_THRESHOLD: 0.8,
  SCORE_2_THRESHOLD: 0.4,
  REQUEST_DELAY_MS: 300,
  AI_ENABLED: true,
  AI_MAX_SCORE: 2,
};

/**
 * Загружает настройки из chrome.storage и патчит CONFIG.
 * Вызывается один раз при старте background worker.
 */
export async function loadConfigFromStorage() {
  return new Promise(resolve => {
    chrome.storage.local.get(
      ["amhub_url", "amhub_token", "groq_api_key", "cf_account_id", "cf_api_token"],
      (data) => {
        if (data.amhub_url) {
          CONFIG.BACKEND_URL = data.amhub_url.replace(/\/$/, "");
          CONFIG.DASHBOARD_QUERIES_URL = CONFIG.BACKEND_URL;
          CONFIG.DASHBOARD_MERCH_RULES_URL = CONFIG.BACKEND_URL;
        }
        if (data.amhub_token)    CONFIG.BACKEND_TOKEN = "Bearer " + data.amhub_token;
        if (data.groq_api_key)   CONFIG.GROQ_API_KEY = data.groq_api_key;
        if (data.cf_account_id)  CONFIG.CF_ACCOUNT_ID = data.cf_account_id;
        if (data.cf_api_token)   CONFIG.CF_API_TOKEN = data.cf_api_token;
        resolve(CONFIG);
      }
    );
  });
}
