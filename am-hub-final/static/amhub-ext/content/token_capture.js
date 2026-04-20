/**
 * token_capture.js — перехват токенов при входе на tbank.ru системы
 * Работает на: time.tbank.ru, tbank.ktalk.ru
 */

(function() {
  const host = window.location.hostname;
  const LOG = (...args) => console.log("[AM Hub token_capture]", ...args);

  let system = null;
  if (host.includes("time.tbank.ru"))   system = "tbank_time";
  if (host.includes("tbank.ktalk.ru"))  system = "ktalk";
  if (!system) return;
  LOG("loaded on", host, "→ system =", system);

  let captured = false;

  function tryCapture(reason) {
    if (captured) return;
    const onLoginPage = /\/login|\/signup|\/reset/i.test(location.pathname);
    const hasLoggedMarker = !!(
      document.querySelector('[class*="user"]') ||
      document.querySelector('[class*="avatar"]') ||
      document.querySelector('[data-testid*="user"]') ||
      document.querySelector('.SidebarHeader') ||
      document.querySelector('#sidebar-header') ||
      document.querySelector('.team-sidebar') ||
      document.querySelector('#channel-header') ||
      document.querySelector('[class*="Sidebar"]')
    );
    const isLoggedIn = !onLoginPage && (hasLoggedMarker || document.cookie.length > 100);
    if (!isLoggedIn) { LOG("not logged in yet (trigger:", reason, ")"); return; }
    captured = true;
    LOG("attempting capture (trigger:", reason, ")");

    chrome.runtime.sendMessage({
      type: "CAPTURE_TOKENS",
      system: system,
      url: window.location.origin,
    }, response => {
      if (chrome.runtime.lastError) {
        LOG("runtime error:", chrome.runtime.lastError.message);
        captured = false;
        return;
      }
      if (response?.ok) {
        LOG(`${system} token captured ok`);
        showBadge("ok", "Токен захвачен");
      } else {
        LOG(`${system} capture failed:`, response?.error);
        showBadge("err", "Не удалось: " + (response?.error || "нет токена"));
        captured = false;
      }
    });
  }

  function showBadge(tone, text) {
    const colors = { ok: "#23d18b", err: "#ef4444" };
    const dot = colors[tone] || "#6474ff";
    const badge = document.createElement("div");
    badge.style.cssText = `
      position:fixed;bottom:16px;right:16px;z-index:999999;
      background:#07090f;border:1px solid rgba(100,116,255,.3);
      border-radius:10px;padding:8px 12px;
      font-family:system-ui,sans-serif;font-size:12px;
      color:#c7d2fe;display:flex;align-items:center;gap:6px;
      box-shadow:0 4px 20px rgba(0,0,0,.5);
    `;
    badge.innerHTML = `<span style="width:6px;height:6px;border-radius:50%;background:${dot};flex-shrink:0"></span>AM Hub: ${text || "OK"}`;
    if (document.body) {
      document.body.appendChild(badge);
      setTimeout(() => badge.remove(), 5000);
    }
  }

  function extractAndNotifyFromLocalStorage() {
    try {
      const keys = ["ktalk_token", "access_token", "token", "auth_token", "MMAUTHTOKEN"];
      for (const k of keys) {
        const v = localStorage.getItem(k) || sessionStorage.getItem(k);
        if (v && v.length > 20) {
          const tokenType = system === "ktalk" ? "ktalk" : "tbank";
          LOG("localStorage key hit:", k);
          chrome.runtime.sendMessage({
            type: "TOKEN_CAPTURED",
            tokenType, token: v, url: window.location.origin, ts: Date.now()
          });
          return true;
        }
      }
    } catch (e) { LOG("ls error:", e); }
    return false;
  }

  function startObserving() {
    if (!document.body) { setTimeout(startObserving, 200); return; }
    tryCapture("initial");
    extractAndNotifyFromLocalStorage();
    const observer = new MutationObserver(() => {
      tryCapture("dom-mutation");
      if (!window.__amhubLsChecked && extractAndNotifyFromLocalStorage()) {
        window.__amhubLsChecked = true;
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => observer.disconnect(), 120000);
    setInterval(() => { captured = false; tryCapture("interval"); }, 5 * 60_000);
  }
  startObserving();
})();
