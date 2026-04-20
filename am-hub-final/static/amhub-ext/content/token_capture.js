/**
 * token_capture.js — перехват токенов при входе на tbank.ru системы
 * Работает на: time.tbank.ru, tbank.ktalk.ru
 * 
 * Стратегия: читает cookies после загрузки страницы и пушит в AM Hub.
 * HttpOnly cookies недоступны из JS — используем chrome.cookies API
 * через message к background worker.
 */

(function() {
  const host = window.location.hostname;
  
  // Определяем какой системе мы на странице
  let system = null;
  if (host.includes("time.tbank.ru"))   system = "tbank_time";
  if (host.includes("tbank.ktalk.ru"))  system = "ktalk";
  if (!system) return;

  // Ждём когда пользователь залогинится (URL меняется или появляется признак авторизации)
  let captured = false;
  let badgeShown = false;   // показываем всплывашку только один раз за загрузку страницы

  function tryCapture() {
    if (captured) return;

    // Проверяем признаки авторизации
    const isLoggedIn = (
      document.querySelector('[class*="user"]') ||
      document.querySelector('[class*="avatar"]') ||
      document.querySelector('[class*="profile"]') ||
      document.querySelector('[data-testid*="user"]') ||
      // Для KTalk — header с именем пользователя
      document.querySelector('.SidebarHeader') ||
      // Для Time — Mattermost топбар
      document.querySelector('#sidebar-header') ||
      document.querySelector('.team-sidebar')
    );

    if (!isLoggedIn) return;
    captured = true;

    // Запрашиваем background worker достать cookies
    chrome.runtime.sendMessage({
      type: "CAPTURE_TOKENS",
      system: system,
      url: window.location.origin,
    }, response => {
      if (response?.ok) {
        console.log(`[AM Hub] ${system} токен захвачен автоматически`);
        if (!badgeShown) {
          showBadge();
          badgeShown = true;
        }
      }
    });
  }

  function showBadge() {
    // Маленький badge что AM Hub получил токен
    const badge = document.createElement("div");
    badge.style.cssText = `
      position:fixed;bottom:16px;right:16px;z-index:999999;
      background:#07090f;border:1px solid rgba(100,116,255,.3);
      border-radius:10px;padding:8px 12px;
      font-family:system-ui,sans-serif;font-size:12px;
      color:#6474ff;display:flex;align-items:center;gap:6px;
      box-shadow:0 4px 20px rgba(0,0,0,.5);
    `;
    badge.innerHTML = `
      <span style="width:6px;height:6px;border-radius:50%;background:#23d18b;flex-shrink:0"></span>
      AM Hub: токен получен
    `;
    document.body.appendChild(badge);
    setTimeout(() => badge.remove(), 3000);
  }

  // Проверяем сразу и при изменениях DOM
  tryCapture();
  const observer = new MutationObserver(tryCapture);
  observer.observe(document.body, { childList: true, subtree: true });

  // Останавливаем первичное наблюдение через 30 сек
  setTimeout(() => observer.disconnect(), 30000);

  // Каждые 5 минут повторно пробуем захватить токен — SSO может обновить его,
  // и cookie/localStorage будут обновлены. Сбрасываем флаг captured, чтобы дать
  // tryCapture() ещё один проход.
  setInterval(() => {
    captured = false;
    tryCapture();
  }, 5 * 60 * 1000);
})();
