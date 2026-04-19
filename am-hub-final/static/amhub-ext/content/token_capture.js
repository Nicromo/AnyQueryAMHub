/**
 * token_capture.js — перехват токенов при входе на tbank.ru системы
 * Работает на: time.tbank.ru, tbank.ktalk.ru
 *
 * Стратегия:
 *   1. Обнаруживаем залогиненного пользователя (есть признаки в DOM).
 *   2. Считываем токен:
 *        • tbank.ktalk.ru → localStorage/sessionStorage (access_token и т.п.)
 *        • time.tbank.ru  → куки достаются background'ом (HttpOnly)
 *   3. Сохраняем токен в chrome.storage.local (last_ktalk_token / last_time_token).
 *   4. Отправляем в background через {type:'TOKEN_CAPTURED', ...} — тот уже пушит в AM Hub.
 */

(function () {
  const host = (typeof window !== "undefined" && window.location && window.location.hostname) || "";

  let tokenType = null;             // 'ktalk' | 'tbank'
  let legacySystem = null;          // для старого handler'а CAPTURE_TOKENS (куки)
  if (host.includes("time.tbank.ru"))  { tokenType = "tbank"; legacySystem = "tbank_time"; }
  if (host.includes("tbank.ktalk.ru")) { tokenType = "ktalk"; legacySystem = "ktalk"; }
  if (!tokenType) return;

  let captured = false;
  let observer = null;

  function readLocalToken() {
    try {
      const keys = ["access_token", "ktalk_token", "token", "authToken", "auth_token"];
      for (const k of keys) {
        const v = localStorage.getItem(k);
        if (v && v.length > 8) return v;
      }
      for (const k of keys) {
        const v = sessionStorage.getItem(k);
        if (v && v.length > 8) return v;
      }
    } catch {}
    return null;
  }

  function isLoggedIn() {
    if (!document || !document.body) return false;
    return !!(
      document.querySelector('[class*="user"]') ||
      document.querySelector('[class*="avatar"]') ||
      document.querySelector('[class*="profile"]') ||
      document.querySelector('[data-testid*="user"]') ||
      document.querySelector(".SidebarHeader") ||
      document.querySelector("#sidebar-header") ||
      document.querySelector(".team-sidebar")
    );
  }

  function showBadge(message) {
    if (!document.body) return;
    const badge = document.createElement("div");
    badge.style.cssText = [
      "position:fixed","bottom:16px","right:16px","z-index:999999",
      "background:#07090f","border:1px solid rgba(100,116,255,.3)",
      "border-radius:10px","padding:8px 12px",
      "font-family:system-ui,sans-serif","font-size:12px",
      "color:#6474ff","display:flex","align-items:center","gap:6px",
      "box-shadow:0 4px 20px rgba(0,0,0,.5)",
    ].join(";");
    badge.innerHTML =
      '<span style="width:6px;height:6px;border-radius:50%;background:#23d18b;flex-shrink:0"></span>' +
      (message || "AM Hub: токен получен");
    document.body.appendChild(badge);
    setTimeout(() => badge.remove(), 3000);
  }

  function sendTokenCaptured(token) {
    // 1) фронтовой путь: ktalk — токен уже есть на руках, шлём TOKEN_CAPTURED
    if (token) {
      try {
        chrome.runtime.sendMessage(
          { type: "TOKEN_CAPTURED", tokenType, token, url: window.location.origin, ts: Date.now() },
          (resp) => {
            if (chrome.runtime.lastError) {
              console.warn("[AM Hub] TOKEN_CAPTURED sendMessage error:", chrome.runtime.lastError.message);
              return;
            }
            if (resp && resp.ok) {
              console.log(`[AM Hub] ${tokenType} токен отправлен в хаб`);
              showBadge(`AM Hub: токен ${tokenType} обновлён`);
            }
          }
        );
      } catch (e) {
        console.warn("[AM Hub] sendMessage throw:", e);
      }
      return;
    }

    // 2) legacy путь: tbank — токен HttpOnly, пусть background достанет из куков
    //    background сам сохранит в storage + пушит в хаб + отправит TOKEN_CAPTURED self-handle
    try {
      chrome.runtime.sendMessage(
        { type: "CAPTURE_TOKENS", system: legacySystem, url: window.location.origin },
        (resp) => {
          if (chrome.runtime.lastError) return;
          if (resp && resp.ok) showBadge(`AM Hub: токен ${tokenType} обновлён`);
        }
      );
    } catch {}
  }

  function tryCapture() {
    if (captured) return;
    if (!document.body) return;             // защита от раннего run
    if (!isLoggedIn()) return;

    const token = readLocalToken();
    // Для ktalk пытаемся взять из localStorage; если не вышло — пускай background смотрит куки.
    // Для tbank_time — HttpOnly-куки, всегда через background.
    if (tokenType === "ktalk" && token) {
      captured = true;
      sendTokenCaptured(token);
    } else if (tokenType === "tbank") {
      captured = true;
      sendTokenCaptured(null);
    }
  }

  function startObserver() {
    if (!document.body) {
      // повтор после готовности DOM
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startObserver, { once: true });
      } else {
        setTimeout(startObserver, 100);
      }
      return;
    }
    tryCapture();
    try {
      observer = new MutationObserver(tryCapture);
      observer.observe(document.body, { childList: true, subtree: true });
    } catch (e) {
      console.warn("[AM Hub] observer error:", e);
    }
    setTimeout(() => { if (observer) observer.disconnect(); }, 30000);
  }

  startObserver();
})();
