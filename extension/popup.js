// popup.js — логика расширения AM Hub · Sync (MV3).
// Вынесена из inline <script> в popup.html для CSP-совместимости.

(function () {
  "use strict";

  // ── Константы / хелперы ────────────────────────────────────────
  var DEFAULT_HUB_URL = "https://hub.any-platform.ru";
  var hasChrome  = typeof chrome !== "undefined" && !!chrome.storage;
  var hasRuntime = hasChrome && !!chrome.runtime && !!chrome.runtime.sendMessage;
  var hasTabs    = hasChrome && !!chrome.tabs && !!chrome.tabs.create;

  var $ = function (id) { return document.getElementById(id); };

  function fmtTime(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts);
      var hh = String(d.getHours()).padStart(2, "0");
      var mm = String(d.getMinutes()).padStart(2, "0");
      return hh + ":" + mm;
    } catch (e) { return ""; }
  }

  function humanAgo(ts) {
    if (!ts) return "";
    var sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (sec < 60)  return "только что";
    if (sec < 3600) return Math.floor(sec / 60) + " мин назад";
    if (sec < 86400) return Math.floor(sec / 3600) + " ч назад";
    return Math.floor(sec / 86400) + " д назад";
  }

  // ── SVG-иконки для 4 состояний (inline, CSP-safe) ──────────────
  var ICONS = {
    circle_check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/></svg>',
    refresh:      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="spin"><path d="M20 4v6h-6M4 20v-6h6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></svg>',
    alert:        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3L2 21h20L12 3zM12 10v5M12 18h.01"/></svg>',
    puzzle:       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 4h4v3a2 2 0 1 0 0 4v4h-3a2 2 0 1 1-4 0H4V4h6zm10 7a2 2 0 1 1-4 0H14v4h6v-4z"/></svg>',
  };

  // ── Статус-карточка ────────────────────────────────────────────
  // tone: "ok" | "signal" | "critical" | "warn"
  function setStatus(tone, title, sub) {
    var card = $("statusCard");
    var icon = $("statusIcon");
    if (!card) return;
    card.setAttribute("data-tone", tone);
    $("statusTitle").textContent = title || "";
    $("statusSub").textContent   = sub || "";

    var iconKey =
      tone === "ok"       ? "circle_check" :
      tone === "signal"   ? "refresh" :
      tone === "critical" ? "alert" :
                            "puzzle";
    icon.innerHTML = ICONS[iconKey];
  }

  // ── Сообщения (error / result) ─────────────────────────────────
  function showError(msg) {
    var e = $("errorBox");
    e.textContent = String(msg || "Неизвестная ошибка").slice(0, 200);
    e.style.display = "block";
    $("resultBox").style.display = "none";
  }
  function showResult(msg) {
    var b = $("resultBox");
    b.textContent = msg || "";
    b.style.display = "block";
    $("errorBox").style.display = "none";
  }
  function clearMessages() {
    $("errorBox").style.display = "none";
    $("resultBox").style.display = "none";
  }

  // ── Stats row и last event ─────────────────────────────────────
  function updateStats(result) {
    var row = $("statsRow");
    if (!result) { row.style.display = "none"; return; }
    $("statClients").textContent = result.clients_synced || 0;
    $("statTasks").textContent   = result.tasks_synced   || 0;
    $("statEvents").textContent  = (result.clients_synced || 0) + (result.tasks_synced || 0);
    row.style.display = "grid";
  }

  // Рендер списка последних 4-х событий из sync_log.
  // entries: массив {ts, tone, message} — свежие первыми.
  function renderLog(entries) {
    var el = $("logList");
    if (!el) return;
    if (!entries || !entries.length) {
      el.innerHTML = '<div class="log-item mono"><span class="log-time">—</span><span>нет событий</span></div>';
      return;
    }
    var html = entries.slice(0, 4).map(function (e) {
      var time   = fmtTime(e.ts) || "—";
      var tone   = e.tone || "neutral";
      var cls    = tone === "ok" ? "log-msg-ok" : "";
      var prefix = tone === "ok" ? "✓ " : tone === "error" ? "✗ " : "· ";
      return '<div class="log-item mono">' +
               '<span class="log-time">' + escapeHtml(time) + '</span>' +
               '<span class="' + cls + '">' + prefix + escapeHtml(String(e.message || "")) + '</span>' +
             '</div>';
    }).join("");
    el.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c];
    });
  }

  // Версия из manifest (а не хардкод в футере)
  function renderVersion() {
    try {
      var v = chrome.runtime && chrome.runtime.getManifest ? chrome.runtime.getManifest().version : null;
      $("versionLabel").textContent = v ? ("v " + v) : "v —";
    } catch (e) { /* silent */ }
  }

  // Форсированный resync (кнопка в header)
  function forceResync() {
    if (!hasRuntime) return;
    clearMessages();
    setStatus("signal", "Синхронизация…", "ручной запуск");
    try {
      chrome.runtime.sendMessage({ action: "sync" }, function () {
        setTimeout(loadStatus, 400);
      });
    } catch (e) { /* silent */ }
  }

  // ── Загрузка настроек из chrome.storage ────────────────────────
  function loadSettings() {
    if (!hasChrome) {
      $("hubUrl").value = DEFAULT_HUB_URL;
      setStatus("warn", "Не настроено", "Chrome API недоступен (превью вне расширения)");
      return;
    }
    chrome.storage.local.get(
      ["mr_login", "mr_password", "hub_url", "hub_token"],
      function (s) {
        if (s.mr_login)   $("mrLogin").value = s.mr_login;
        if (s.mr_password) $("mrPass").value = s.mr_password;
        $("hubUrl").value = s.hub_url || DEFAULT_HUB_URL;
        if (s.hub_token)  $("hubToken").value = s.hub_token;

        var ready = s.mr_login && s.mr_password && (s.hub_url || DEFAULT_HUB_URL) && s.hub_token;
        if (ready) {
          setStatus("ok", "Подключено", "Настройки загружены");
        } else {
          setStatus("warn", "Не настроено", "Заполните поля ниже");
        }
      }
    );
  }

  // ── Получение статуса из background.js (polling каждые 3с) ─────
  function loadStatus() {
    if (!hasRuntime) return;
    try {
      chrome.runtime.sendMessage({ action: "getStatus" }, function (res) {
        if (chrome.runtime.lastError || !res) return;

        if (res.status === "ok") {
          var result = res.last_result || {};
          var extra = result.clients_synced != null
            ? "клиенты: " + (result.clients_synced || 0) + " · задачи: " + (result.tasks_synced || 0)
            : "";
          setStatus("ok", "Подключено",
            (res.last_sync ? "синхр. " + humanAgo(res.last_sync) : "") +
            (extra ? " · " + extra : "")
          );
          updateStats(result);
        } else if (res.status === "error") {
          var errMsg = res.error ? String(res.error).slice(0, 80) : "Неизвестная ошибка";
          setStatus("critical", "Ошибка", errMsg);
          if (res.error) showError(res.error);
        } else if (res.status === "running") {
          setStatus("signal", "Синхронизация…",
            res.last_sync ? "запущена в " + fmtTime(res.last_sync) : "подключаемся…"
          );
        }
        // status === "idle" — оставляем то, что выставил loadSettings

        // Лог событий — всегда обновляем из sync_log
        renderLog(res.log || []);
      });
    } catch (e) { /* silent fallback */ }
  }

  // ── Сохранение и запуск синхронизации ──────────────────────────
  function save() {
    clearMessages();
    var login = $("mrLogin").value.trim();
    var pass  = $("mrPass").value;
    var url   = $("hubUrl").value.trim().replace(/\/$/, "") || DEFAULT_HUB_URL;
    var token = $("hubToken").value.trim();

    if (!login || !pass || !url || !token) {
      showError("Заполните все 4 поля");
      setStatus("warn", "Не настроено", "заполните поля ниже");
      return;
    }
    if (!hasChrome) {
      showError("Chrome API недоступен — превью popup.html вне расширения.");
      return;
    }

    chrome.storage.local.set({
      mr_login: login,
      mr_password: pass,
      hub_url: url,
      hub_token: token,
    }, function () {
      showResult("Настройки сохранены, запускаю синхронизацию…");
      setStatus("signal", "Синхронизация…", "отправлено в background");
      if (hasRuntime) {
        try {
          chrome.runtime.sendMessage({ action: "sync" }, function () {
            setTimeout(loadStatus, 400);
          });
        } catch (e) { /* silent */ }
      }
    });
  }

  function openHub() {
    var url = ($("hubUrl").value.trim().replace(/\/$/, "")) || DEFAULT_HUB_URL;
    if (hasTabs) {
      chrome.tabs.create({ url: url });
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  // ── Инициализация (после DOM) ──────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    // Бинды событий вместо inline onclick (CSP-safe)
    $("saveBtn").addEventListener("click", save);
    $("openBtn").addEventListener("click", openHub);
    var resync = $("resyncBtn");
    if (resync) resync.addEventListener("click", forceResync);

    renderVersion();
    loadSettings();
    loadStatus();
    if (hasRuntime) setInterval(loadStatus, 3000);
  });
})();
