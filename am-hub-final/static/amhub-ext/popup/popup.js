/**
 * popup.js — логика единого AM Hub popup
 * Fixed: safeSend wrapper, defensive wireEvents, timeout handling, tab switching
 */

// jsPDF loaded globally via <script src="../vendor/jspdf.umd.min.js"> in popup.html

// ── Safe message sender (handles inactive service worker + timeout) ────────────
async function safeSend(msg, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ ok: false, error: "timeout" }), timeoutMs);
    try {
      chrome.runtime.sendMessage(msg, (response) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response || { ok: false, error: "no response" });
        }
      });
    } catch (e) {
      clearTimeout(timer);
      resolve({ ok: false, error: String(e.message || e) });
    }
  });
}

// ── Wake up service worker before sending real messages ───────────────────────
async function wakeUpBackground() {
  // Send a dummy ping first — if the SW is sleeping, this starts it up.
  // We don't care about the response here.
  await safeSend({ type: "PING" }, 2000);
}

// ── Tab switching ─────────────────────────────────────────────────────────────
const TABS = ["sync", "checkup", "settings"];
function switchTab(tab, btn) {
  TABS.forEach(t => {
    const panel = document.getElementById(`t-${t}`);
    if (panel) panel.classList.toggle("hidden", t !== tab);
  });
  document.querySelectorAll(".tab").forEach(b => b.classList.remove("act"));
  if (btn) btn.classList.add("act");
  if (tab === "sync") refreshSyncStatus();
}
window.switchTab = switchTab;

// ── Init ──────────────────────────────────────────────────────────────────────
// Показать версию + когда собрано. Читаем build-info.json из пакета расширения.
async function showBuildInfo() {
  const el = document.getElementById("build-info");
  if (!el) return;
  // Version из manifest — всегда есть.
  const mf = chrome.runtime.getManifest();
  let text = `v${mf.version}`;
  try {
    const r = await fetch(chrome.runtime.getURL("build-info.json"));
    if (r.ok) {
      const info = await r.json();
      // Показываем "v3.1.0 · 19 апр 12:20" + commit в tooltip
      const d = new Date(info.built_at);
      const dateStr = d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      text = `v${info.version} · ${dateStr}`;
      el.title = `Версия: ${info.version}\nСобрано: ${info.built_at}\nCommit: ${info.commit}`;
    }
  } catch (e) { /* нет файла — просто покажем версию из manifest */ }
  el.textContent = text;
}

async function init() {
  wireEvents();
  await loadSettings();
  await showBuildInfo();

  // Wake the SW before doing connection check — avoids first-open hang
  await wakeUpBackground();

  checkHubConnection();
  refreshSyncStatus();
  refreshTokenStatus();

  // Listen for checkup progress messages from background
  try {
    chrome.runtime.onMessage.addListener(msg => {
      if (msg && msg.type === "PROGRESS") updateProgress(msg.current, msg.total);
    });
  } catch (e) {
    // If we can't add listener, progress just won't update — non-fatal
  }
}

// ── Event wiring (CSP-safe, no inline handlers) ───────────────────────────────
function wireEvents() {
  // Main tabs
  document.querySelectorAll(".tab[data-t]").forEach(el => {
    if (el) el.addEventListener("click", () => switchTab(el.dataset.t, el));
  });

  // Checkup query-type tabs
  document.querySelectorAll(".qtab[data-qt]").forEach(el => {
    if (el) el.addEventListener("click", () => setQType(el.dataset.qt, el));
  });

  // data-action buttons
  const actions = {
    syncNow, openHub, openTime, openKtalk,
    loadCabinet, runCheck, runCal, genPDF, exportCSV,
    saveSettings, testMR,
  };
  document.querySelectorAll("[data-action]").forEach(el => {
    if (!el) return;
    const fn = actions[el.dataset.action];
    if (fn) el.addEventListener("click", fn);
  });

  // Delegated: product chips (injected dynamically)
  const ckProducts = document.getElementById("ck-products");
  if (ckProducts) {
    ckProducts.addEventListener("click", e => {
      const chip = e.target.closest(".prod-chip[data-product]");
      if (chip) setProduct(chip.dataset.product, chip);
    });
  }
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const s = await chrome.storage.local.get([
      "hub_url", "hub_token", "mr_login", "mr_password", "groq_api_key", "managerName"
    ]);
    const set = (id, val) => { const el = document.getElementById(id); if (el && val) el.value = val; };
    set("s-hub-url",   s.hub_url);
    set("s-hub-token", s.hub_token);
    set("s-mr-login",  s.mr_login);
    set("s-mr-pass",   s.mr_password);
    set("s-groq",      s.groq_api_key);
    set("s-manager",   s.managerName);
  } catch (e) {
    // Storage not available yet — non-fatal
  }
}

async function saveSettings() {
  const get = id => { const el = document.getElementById(id); return el ? el.value : ""; };
  const data = {
    hub_url:      get("s-hub-url").trim().replace(/\/$/, ""),
    hub_token:    get("s-hub-token").trim(),
    mr_login:     get("s-mr-login").trim(),
    mr_password:  get("s-mr-pass"),
    groq_api_key: get("s-groq").trim(),
    managerName:  get("s-manager").trim(),
  };
  try {
    await chrome.storage.local.set(data);
  } catch (e) {
    showBox("s-result", "❌ Ошибка сохранения: " + e.message, "err");
    return;
  }

  // Notify background — errors here are non-fatal
  const reloadRes = await safeSend({ type: "RELOAD_CONFIG" });
  if (reloadRes.error === "timeout" || reloadRes.error === "no response") {
    // SW was sleeping — data is saved, just couldn't notify SW; show warning
    showBox("s-result", "✅ Сохранено (расширение активируется — перезапустите при необходимости)", "ok");
  } else {
    await safeSend({ type: "SET_MANAGER_NAME", name: data.managerName });
    showBox("s-result", "✅ Сохранено", "ok");
  }

  // Keep success message visible for 3 seconds then hide
  setTimeout(() => {
    const el = document.getElementById("s-result");
    if (el) el.classList.add("hidden");
  }, 3000);

  checkHubConnection();
}

// ── Hub connection ────────────────────────────────────────────────────────────
async function checkHubConnection() {
  const dot = document.getElementById("hd");
  const lbl = document.getElementById("hs");
  if (!dot || !lbl) return;

  dot.className = "dot dot-run";
  lbl.textContent = "Проверка...";

  const res = await safeSend({ type: "CHECK_CONNECTION" }, 8000);

  if (!res || res.error) {
    // Could not reach background SW
    dot.className = "dot dot-err";
    if (res && res.error === "timeout") {
      lbl.textContent = "Расширение активируется... перезапустите";
    } else {
      lbl.textContent = res?.error || "Нет связи";
    }
    return;
  }

  if (res.ok) {
    dot.className = "dot dot-ok";
    lbl.textContent = res.user?.name || "Подключено";
  } else {
    dot.className = "dot dot-err";
    lbl.textContent = res.error || "Нет связи";
  }
}

// ── Sync ──────────────────────────────────────────────────────────────────────
async function refreshSyncStatus() {
  const res = await safeSend({ type: "GET_SYNC_STATUS" });

  const dot = document.getElementById("sync-dot");
  const lbl = document.getElementById("sync-label");
  const sub = document.getElementById("sync-sub");
  if (!dot || !lbl || !sub) return;

  if (!res || res.error) {
    dot.className = "dot dot-idle";
    lbl.textContent = "Ожидание";
    sub.textContent = "Авто каждые 30 мин";
    return;
  }

  const statuses = { ok: "dot-ok", error: "dot-err", running: "dot-run", idle: "dot-idle" };
  dot.className = "dot " + (statuses[res.status] || "dot-idle");
  const labels = { ok: "Синхронизировано", error: "Ошибка", running: "Синхронизация...", idle: "Ожидание" };
  lbl.textContent = labels[res.status] || "Ожидание";
  sub.textContent = res.lastSync ? `Последний: ${res.lastSync}` : "Авто каждые 30 мин";

  if (res.error) showBox("sync-result", "❌ " + res.error, "err");
  else if (res.lastResult) {
    showBox("sync-result", `✅ ${res.lastResult.clients_synced || 0} клиентов · ${res.lastResult.tasks_synced || 0} задач`, "ok");
  }
}

async function syncNow() {
  const dot = document.getElementById("sync-dot");
  const lbl = document.getElementById("sync-label");
  if (dot) dot.className = "dot dot-run";
  if (lbl) lbl.textContent = "Синхронизация...";
  showBox("sync-result", "⏳ Идёт синхронизация...", "warn");

  const res = await safeSend({ type: "SYNC_NOW" }, 30000);
  if (res && res.error === "timeout") {
    showBox("sync-result", "⏳ Синхронизация запущена в фоне", "warn");
  }
  refreshSyncStatus();
}

function openHub() {
  chrome.storage.local.get("hub_url", d => {
    const url = (d && d.hub_url) || "https://anyqueryamhub-production-9654.up.railway.app/design/command";
    chrome.tabs.create({ url });
  });
}

// ── Token status ──────────────────────────────────────────────────────────────
async function refreshTokenStatus() {
  try {
    const s = await chrome.storage.local.get(["last_time_token", "last_ktalk_token"]);

    const timeDot = document.getElementById("tk-time-dot");
    const timeSub = document.getElementById("tk-time-sub");
    const ktalkDot = document.getElementById("tk-ktalk-dot");
    const ktalkSub = document.getElementById("tk-ktalk-sub");
    const hint = document.getElementById("tk-hint");

    if (timeDot && timeSub) {
      if (s && s.last_time_token) {
        timeDot.className = "dot dot-ok";
        timeSub.textContent = "Активен · обновится при следующем входе";
      } else {
        timeDot.className = "dot dot-idle";
        timeSub.textContent = "Войдите в time.tbank.ru";
        if (hint) hint.classList.remove("hidden");
      }
    }
    if (ktalkDot && ktalkSub) {
      if (s && s.last_ktalk_token) {
        ktalkDot.className = "dot dot-ok";
        ktalkSub.textContent = "Активен";
      } else {
        ktalkDot.className = "dot dot-idle";
        ktalkSub.textContent = "Войдите в tbank.ktalk.ru";
      }
    }
  } catch (e) {
    // Storage not available — non-fatal
  }
}

function openTime()  { chrome.tabs.create({ url: "https://time.tbank.ru" }); }
function openKtalk() { chrome.tabs.create({ url: "https://tbank.ktalk.ru" }); }

// ── Checkup ───────────────────────────────────────────────────────────────────
let ckResults = [];

async function loadCabinet() {
  const cabEl = document.getElementById("ck-cabinet");
  const id = cabEl ? cabEl.value.trim() : "";
  if (!id) return;

  showAlert("⏳ Загружаем данные кабинета...", "warn");
  const res = await safeSend({ type: "LOAD_CABINET", cabinetId: id });
  if (!res || res.error || !res.ok) {
    showAlert("❌ " + (res?.error || "Нет ответа от расширения"), "err");
    return;
  }

  const clientNameEl = document.getElementById("ck-client-name");
  if (clientNameEl) clientNameEl.textContent = res.clientName || "";

  // Render product chips
  const prodEl = document.getElementById("ck-products");
  if (prodEl) {
    const products = res.availableProducts || [];
    if (products.length) {
      const labels = { sort: "🔍 Sort", autocomplete: "⌨ Auto", recommendations: "⭐ Rec" };
      const cls    = { sort: "p-sort", autocomplete: "p-auto", recommendations: "p-rec" };
      prodEl.innerHTML = products.map((p, i) =>
        `<span class="prod-chip ${cls[p] || ''} ${i === 0 ? 'prod-act' : ''}" data-product="${p}">${labels[p] || p}</span>`
      ).join("");
    }
  }

  // Load queries
  const activeQTab = document.querySelector(".qtab.act");
  const qt = activeQTab ? (activeQTab.dataset.qt || "top") : "top";
  const qr = await safeSend({ type: "LOAD_QUERIES", cabinetId: id, queryType: qt });
  if (qr && qr.ok && qr.queries?.length) {
    const qEl = document.getElementById("ck-queries");
    if (qEl) qEl.value = qr.queries.map(q => typeof q === "string" ? q : q.query).join("\n");
  }
  showAlert("", "");
}

function setProduct(product, el) {
  document.querySelectorAll(".prod-chip").forEach(c => c.classList.remove("prod-act"));
  if (el) el.classList.add("prod-act");
  safeSend({ type: "SET_ACTIVE_PRODUCT", product });
}

function setQType(qt, btn) {
  document.querySelectorAll(".qtab").forEach(b => b.classList.remove("act"));
  if (btn) btn.classList.add("act");
  safeSend({ type: "SET_QUERY_TYPE", queryType: qt });
}

async function runCal() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) { showAlert("❌ Нет активной вкладки", "err"); return; }
    const res = await safeSend({ type: "RUN_CALIBRATION", tabId: tab.id });
    if (!res || res.error) { showAlert("❌ " + (res?.error || "Нет ответа"), "err"); return; }
    if (res.mode === "api")        showAlert("✅ API-режим — выдача совпадает", "ok");
    else if (res.mode === "site")  showAlert("⚠️ Сайт-режим — клиент ранжирует сам", "warn");
    else if (res.needSelector)     showAlert("❓ Укажите CSS-селектор товаров", "warn");
    else showAlert(res.message || (res.ok ? "OK" : res.error), res.ok ? "ok" : "err");
  } catch (e) {
    showAlert("❌ " + e.message, "err");
  }
}

async function runCheck() {
  const rawQEl = document.getElementById("ck-queries");
  const rawQ = rawQEl ? rawQEl.value.trim() : "";
  if (!rawQ) { showAlert("Введите запросы", "warn"); return; }
  const queries = rawQ.split("\n").map(q => q.trim()).filter(Boolean);

  await safeSend({ type: "SET_QUERIES", queries });

  const ckSetup    = document.getElementById("ck-setup");
  const ckProgress = document.getElementById("ck-progress");
  const ckResultsEl = document.getElementById("ck-results");

  if (ckSetup)    ckSetup.classList.add("hidden");
  if (ckProgress) ckProgress.classList.remove("hidden");
  if (ckResultsEl) ckResultsEl.classList.add("hidden");
  updateProgress(0, queries.length);

  const res = await safeSend({ type: "RUN_CHECK" }, 120000); // 2 min for long checks
  ckResults = (res && res.results) ? res.results : [];

  if (ckProgress) ckProgress.classList.add("hidden");
  if (ckSetup)    ckSetup.classList.remove("hidden");

  if (!res || res.error) {
    showAlert("❌ " + (res?.error || "Нет ответа от расширения"), "err");
    return;
  }

  if (ckResultsEl) ckResultsEl.classList.remove("hidden");
  renderResults(ckResults);
}

function updateProgress(cur, total) {
  const progF = document.getElementById("ck-prog-f");
  const progT = document.getElementById("ck-prog-t");
  const pct = total ? Math.round(cur / total * 100) : 0;
  if (progF) progF.style.width = pct + "%";
  if (progT) progT.textContent = `${cur} / ${total} запросов`;
}

function renderResults(results) {
  if (!results || !results.length) return;

  const dist = { 0: 0, 1: 0, 2: 0, 3: 0 };
  results.forEach(r => {
    const sc = r.manualScore ?? r.autoScore;
    if (sc in dist) dist[sc]++;
  });
  const avg = results.length
    ? (results.reduce((s, r) => s + (r.manualScore ?? r.autoScore), 0) / results.length).toFixed(2)
    : "—";

  const statsEl = document.getElementById("ck-stats");
  if (statsEl) {
    statsEl.innerHTML = `
      <div class="sc s0"><div class="n">${dist[0]}</div><div class="l">Оц.0</div></div>
      <div class="sc s1"><div class="n">${dist[1]}</div><div class="l">Оц.1</div></div>
      <div class="sc s2"><div class="n">${dist[2]}</div><div class="l">Оц.2</div></div>
      <div class="sc s3"><div class="n">${dist[3]}</div><div class="l">Оц.3</div></div>
      <div class="sc sa"><div class="n">${avg}</div><div class="l">Среднее</div></div>
    `;
  }

  const BCLS = ["b0", "b1", "b2", "b3"];
  const listEl = document.getElementById("ck-result-list");
  if (listEl) {
    listEl.innerHTML = results
      .filter(r => (r.manualScore ?? r.autoScore) < 3)
      .slice(0, 15)
      .map(r => {
        const sc = r.manualScore ?? r.autoScore;
        const recs = (r.recommendation || []).map(rec => `<div class="ri-rec">${rec}</div>`).join("");
        const ai   = r.aiRecommendation ? `<div class="ri-rec ai">🤖 ${r.aiRecommendation}</div>` : "";
        return `<div class="ri">
          <div class="ri-top">
            <span class="badge ${BCLS[sc] || 'b0'}">${sc}</span>
            <div class="ri-q">${r.query}</div>
            ${r.impressions ? `<span style="font-size:.65rem;color:#4c567a">${r.impressions}</span>` : ""}
          </div>
          ${r.reason ? `<div class="ri-reason">${r.reason}</div>` : ""}
          ${recs || ai ? `<div class="ri-recs">${recs}${ai}</div>` : ""}
        </div>`;
      }).join("")
      + (results.length > 15 ? `<div style="font-size:.7rem;color:#4c567a;text-align:center;padding:6px">...ещё ${results.length - 15} запросов в отчёте</div>` : "");
  }
}

async function genPDF() {
  if (!ckResults.length) return;

  // Check jsPDF availability with fallback message
  if (!window.jspdf || !window.jspdf.jsPDF) {
    showAlert("❌ jsPDF не загружен. Попробуйте перезапустить расширение.", "err");
    return;
  }

  const state = await safeSend({ type: "GET_CHECKUP_STATE" });
  const { jsPDF: PDF } = window.jspdf;
  const doc = new PDF();
  let y = 20;
  doc.setFontSize(16); doc.text("AM Hub — Search Quality Checkup", 14, y); y += 8;
  doc.setFontSize(10); doc.setTextColor(100);
  doc.text(`Клиент: ${(state && state.clientName) || "—"} · ID: ${(state && state.cabinetId) || "—"} · ${new Date().toLocaleDateString("ru-RU")}`, 14, y); y += 6;
  doc.text(`Менеджер: ${(state && state.managerName) || "—"} · Режим: ${(state && state.mode) || "—"} · Запросов: ${ckResults.length}`, 14, y); y += 10;
  doc.setTextColor(0);
  ckResults.forEach((r, i) => {
    if (y > 270) { doc.addPage(); y = 20; }
    const sc = r.manualScore ?? r.autoScore;
    doc.setFontSize(11); doc.text(`${i + 1}. [${sc}] ${r.query}`, 14, y); y += 6;
    if (r.reason) { doc.setFontSize(9); doc.setTextColor(80); doc.text(r.reason.slice(0, 90), 18, y); y += 5; doc.setTextColor(0); }
    (r.recommendation || []).slice(0, 2).forEach(rec => {
      doc.setFontSize(8); doc.setTextColor(60); doc.text("• " + rec.slice(0, 85), 18, y); y += 4;
    });
    if (r.aiRecommendation) { doc.setTextColor(100, 50, 200); doc.text("🤖 " + r.aiRecommendation.slice(0, 85), 18, y); y += 4; doc.setTextColor(0); }
    y += 2;
  });
  const clientId = (state && state.clientName) || (state && state.cabinetId) || "export";
  doc.save(`checkup_${clientId}_${new Date().toISOString().slice(0, 10)}.pdf`);
}

async function exportCSV() {
  if (!ckResults.length) return;
  const state = await safeSend({ type: "GET_CHECKUP_STATE" });
  const rows = [["#", "Запрос", "Показов", "Оценка", "Всего товаров", "Продукт", "Причина", "Рекомендация", "AI"]];
  ckResults.forEach(r => {
    rows.push([
      r.index, r.query, r.impressions || "", r.manualScore ?? r.autoScore,
      r.total || "", r.product || "", r.reason || "",
      (r.recommendation || []).join("; "), r.aiRecommendation || ""
    ]);
  });
  const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `checkup_${(state && state.clientName) || "export"}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Test MR ───────────────────────────────────────────────────────────────────
async function testMR() {
  // First save current form values so background CONFIG is up-to-date
  const get = id => { const el = document.getElementById(id); return el ? el.value : ""; };
  try {
    await chrome.storage.local.set({
      mr_login:    get("s-mr-login").trim(),
      mr_password: get("s-mr-pass"),
    });
  } catch (e) { /* non-fatal */ }
  await safeSend({ type: "RELOAD_CONFIG" }, 3000);

  showBox("s-mr-result", "⏳ Проверяем Merchrules (до минуты)...", "warn");
  // Mr auth перебирает много комбинаций путей × режимов × полей + verify
  // каждой успешной попытки. 60s — запас на 108+ сетевых запросов.
  const res = await safeSend({ type: "TEST_MR_AUTH" }, 60000);

  if (!res || res.error === "timeout") {
    showBox("s-mr-result", "❌ Расширение не отвечает — перезагрузите popup", "err");
    return;
  }

  showBox("s-mr-result",
    res.ok
      ? `✅ Merchrules OK · найдено аккаунтов: ${res.accounts_total ?? "—"}`
      : "❌ " + (res.error || "Неизвестная ошибка"),
    res.ok ? "ok" : "err"
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function showAlert(msg, type) {
  const el = document.getElementById("ck-alert");
  if (!el) return;
  if (!msg) { el.classList.add("hidden"); return; }
  el.className = "box box-" + type;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function showBox(id, msg, type) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!msg) { el.classList.add("hidden"); return; }
  el.className = "box box-" + type;
  el.textContent = msg;
  el.classList.remove("hidden");
}

init();
