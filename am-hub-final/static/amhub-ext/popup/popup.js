/**
 * popup.js — логика единого AM Hub popup
 */

// jsPDF loaded globally via <script src="../vendor/jspdf.umd.min.js"> in popup.html

// ── Tab switching ─────────────────────────────────────────────────────────────
const TABS = ["sync","checkup","settings"];
window.switchTab = function(tab, btn) {
  TABS.forEach(t => {
    document.getElementById(`t-${t}`).classList.toggle("hidden", t !== tab);
  });
  document.querySelectorAll(".tab").forEach(b => b.classList.remove("act"));
  btn.classList.add("act");
  if (tab === "sync") refreshSyncStatus();
};

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  await loadSettings();
  checkHubConnection();
  refreshSyncStatus();
  refreshTokenStatus();
  // Слушаем прогресс чекапа
  chrome.runtime.onMessage.addListener(msg => {
    if (msg.type === "PROGRESS") updateProgress(msg.current, msg.total);
  });
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  const s = await chrome.storage.local.get([
    "hub_url","hub_token","mr_login","mr_password","groq_api_key","managerName"
  ]);
  if (s.hub_url)     document.getElementById("s-hub-url").value    = s.hub_url;
  if (s.hub_token)   document.getElementById("s-hub-token").value  = s.hub_token;
  if (s.mr_login)    document.getElementById("s-mr-login").value   = s.mr_login;
  if (s.mr_password) document.getElementById("s-mr-pass").value    = s.mr_password;
  if (s.groq_api_key)document.getElementById("s-groq").value       = s.groq_api_key;
  if (s.managerName) document.getElementById("s-manager").value    = s.managerName;
}

window.saveSettings = async function() {
  const data = {
    hub_url:      document.getElementById("s-hub-url").value.trim().replace(/\/$/, ""),
    hub_token:    document.getElementById("s-hub-token").value.trim(),
    mr_login:     document.getElementById("s-mr-login").value.trim(),
    mr_password:  document.getElementById("s-mr-pass").value,
    groq_api_key: document.getElementById("s-groq").value.trim(),
    managerName:  document.getElementById("s-manager").value.trim(),
  };
  await chrome.storage.local.set(data);
  await chrome.runtime.sendMessage({ type: "RELOAD_CONFIG" });
  await chrome.runtime.sendMessage({ type: "SET_MANAGER_NAME", name: data.managerName });
  showBox("s-result", "✅ Сохранено", "ok");
  checkHubConnection();
};

// ── Hub connection ────────────────────────────────────────────────────────────
async function checkHubConnection() {
  const dot = document.getElementById("hd");
  const lbl = document.getElementById("hs");
  dot.className = "dot dot-run"; lbl.textContent = "Проверка...";
  const res = await chrome.runtime.sendMessage({ type: "CHECK_CONNECTION" });
  if (res?.ok) {
    dot.className = "dot dot-ok";
    lbl.textContent = res.user?.name || "Подключено";
  } else {
    dot.className = "dot dot-err";
    lbl.textContent = res?.error || "Нет связи";
  }
}

// ── Sync ──────────────────────────────────────────────────────────────────────
async function refreshSyncStatus() {
  const res = await chrome.runtime.sendMessage({ type: "GET_SYNC_STATUS" });
  const dot = document.getElementById("sync-dot");
  const lbl = document.getElementById("sync-label");
  const sub = document.getElementById("sync-sub");
  const statuses = { ok:"dot-ok", error:"dot-err", running:"dot-run", idle:"dot-idle" };
  dot.className = "dot " + (statuses[res.status] || "dot-idle");
  const labels = { ok:"Синхронизировано", error:"Ошибка", running:"Синхронизация...", idle:"Ожидание" };
  lbl.textContent = labels[res.status] || "Ожидание";
  sub.textContent = res.lastSync ? `Последний: ${res.lastSync}` : "Авто каждые 30 мин";
  if (res.error) showBox("sync-result", "❌ " + res.error, "err");
  else if (res.lastResult) {
    showBox("sync-result", `✅ ${res.lastResult.clients_synced||0} клиентов · ${res.lastResult.tasks_synced||0} задач`, "ok");
  }
}

window.syncNow = async function() {
  document.getElementById("sync-dot").className = "dot dot-run";
  document.getElementById("sync-label").textContent = "Синхронизация...";
  showBox("sync-result", "⏳ Идёт синхронизация...", "warn");
  const res = await chrome.runtime.sendMessage({ type: "SYNC_NOW" });
  refreshSyncStatus();
};

window.openHub = function() {
  chrome.storage.local.get("hub_url", d => {
    if (d.hub_url) chrome.tabs.create({ url: d.hub_url });
  });
};

// ── Token status ──────────────────────────────────────────────────────────────
async function refreshTokenStatus() {
  // Проверяем есть ли токены в storage
  const s = await chrome.storage.local.get(["last_time_token", "last_ktalk_token"]);
  
  const timeDot = document.getElementById("tk-time-dot");
  const timeSub = document.getElementById("tk-time-sub");
  const ktalkDot = document.getElementById("tk-ktalk-dot");
  const ktalkSub = document.getElementById("tk-ktalk-sub");

  if (s.last_time_token) {
    timeDot.className = "dot dot-ok";
    timeSub.textContent = "Активен · обновится при следующем входе";
  } else {
    timeDot.className = "dot dot-idle";
    timeSub.textContent = "Войдите в time.tbank.ru";
    document.getElementById("tk-hint").classList.remove("hidden");
  }
  if (s.last_ktalk_token) {
    ktalkDot.className = "dot dot-ok";
    ktalkSub.textContent = "Активен";
  } else {
    ktalkDot.className = "dot dot-idle";
    ktalkSub.textContent = "Войдите в tbank.ktalk.ru";
  }
}

window.openTime  = () => chrome.tabs.create({ url: "https://time.tbank.ru" });
window.openKtalk = () => chrome.tabs.create({ url: "https://tbank.ktalk.ru" });

// ── Checkup ───────────────────────────────────────────────────────────────────
let ckResults = [];

window.loadCabinet = async function() {
  const id = document.getElementById("ck-cabinet").value.trim();
  if (!id) return;
  showAlert("⏳ Загружаем данные кабинета...", "warn");
  const res = await chrome.runtime.sendMessage({ type: "LOAD_CABINET", cabinetId: id });
  if (!res.ok) { showAlert("❌ " + res.error, "err"); return; }

  document.getElementById("ck-client-name").textContent = res.clientName || "";

  // Рендерим продукты
  const prodEl = document.getElementById("ck-products");
  const products = res.availableProducts || [];
  if (products.length) {
    const labels = { sort: "🔍 Sort", autocomplete: "⌨ Auto", recommendations: "⭐ Rec" };
    const cls    = { sort: "p-sort", autocomplete: "p-auto", recommendations: "p-rec" };
    prodEl.innerHTML = products.map((p, i) =>
      `<span class="prod-chip ${cls[p]||''} ${i===0?'prod-act':''}" onclick="setProduct('${p}',this)">${labels[p]||p}</span>`
    ).join("");
  }

  // Загружаем запросы
  const qt = document.querySelector(".qtab.act")?.dataset.qt || "top";
  const qr = await chrome.runtime.sendMessage({ type: "LOAD_QUERIES", cabinetId: id, queryType: qt });
  if (qr.ok && qr.queries?.length) {
    document.getElementById("ck-queries").value = qr.queries.map(q => typeof q === "string" ? q : q.query).join("\n");
  }
  showAlert("", "");
};

window.setProduct = function(product, el) {
  document.querySelectorAll(".prod-chip").forEach(c => c.classList.remove("prod-act"));
  el.classList.add("prod-act");
  chrome.runtime.sendMessage({ type: "SET_ACTIVE_PRODUCT", product });
};

window.setQType = function(qt, btn) {
  document.querySelectorAll(".qtab").forEach(b => b.classList.remove("act"));
  btn.classList.add("act");
  chrome.runtime.sendMessage({ type: "SET_QUERY_TYPE", queryType: qt });
};

window.runCal = async function() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const res = await chrome.runtime.sendMessage({ type: "RUN_CALIBRATION", tabId: tab.id });
  if (res.mode === "api")  showAlert("✅ API-режим — выдача совпадает", "ok");
  else if (res.mode === "site") showAlert("⚠️ Сайт-режим — клиент ранжирует сам", "warn");
  else if (res.needSelector) showAlert("❓ Укажите CSS-селектор товаров", "warn");
  else showAlert(res.message || (res.ok ? "OK" : res.error), res.ok ? "ok" : "err");
};

window.runCheck = async function() {
  const rawQ = document.getElementById("ck-queries").value.trim();
  if (!rawQ) { showAlert("Введите запросы", "warn"); return; }
  const queries = rawQ.split("\n").map(q => q.trim()).filter(Boolean);
  await chrome.runtime.sendMessage({ type: "SET_QUERIES", queries });

  document.getElementById("ck-setup").classList.add("hidden");
  document.getElementById("ck-progress").classList.remove("hidden");
  document.getElementById("ck-results").classList.add("hidden");
  updateProgress(0, queries.length);

  const res = await chrome.runtime.sendMessage({ type: "RUN_CHECK" });
  ckResults = res.results || [];
  document.getElementById("ck-progress").classList.add("hidden");
  document.getElementById("ck-setup").classList.remove("hidden");
  document.getElementById("ck-results").classList.remove("hidden");
  renderResults(ckResults);
};

function updateProgress(cur, total) {
  const pct = total ? Math.round(cur / total * 100) : 0;
  document.getElementById("ck-prog-f").style.width = pct + "%";
  document.getElementById("ck-prog-t").textContent = `${cur} / ${total} запросов`;
}

function renderResults(results) {
  const dist = {0:0, 1:0, 2:0, 3:0};
  results.forEach(r => dist[r.manualScore ?? r.autoScore]++);
  const avg = results.length ? (results.reduce((s,r) => s + (r.manualScore ?? r.autoScore), 0) / results.length).toFixed(2) : "—";

  document.getElementById("ck-stats").innerHTML = `
    <div class="sc s0"><div class="n">${dist[0]}</div><div class="l">Оц.0</div></div>
    <div class="sc s1"><div class="n">${dist[1]}</div><div class="l">Оц.1</div></div>
    <div class="sc s2"><div class="n">${dist[2]}</div><div class="l">Оц.2</div></div>
    <div class="sc s3"><div class="n">${dist[3]}</div><div class="l">Оц.3</div></div>
    <div class="sc sa"><div class="n">${avg}</div><div class="l">Среднее</div></div>
  `;

  const BCLS = ["b0","b1","b2","b3"];
  document.getElementById("ck-result-list").innerHTML = results
    .filter(r => (r.manualScore ?? r.autoScore) < 3)
    .slice(0, 15)
    .map(r => {
      const sc = r.manualScore ?? r.autoScore;
      const recs = (r.recommendation || []).map(rec => `<div class="ri-rec">${rec}</div>`).join("");
      const ai   = r.aiRecommendation ? `<div class="ri-rec ai">🤖 ${r.aiRecommendation}</div>` : "";
      return `<div class="ri">
        <div class="ri-top">
          <span class="badge ${BCLS[sc]}">${sc}</span>
          <div class="ri-q">${r.query}</div>
          ${r.impressions ? `<span style="font-size:.65rem;color:#4c567a">${r.impressions}</span>` : ""}
        </div>
        ${r.reason ? `<div class="ri-reason">${r.reason}</div>` : ""}
        ${recs||ai ? `<div class="ri-recs">${recs}${ai}</div>` : ""}
      </div>`;
    }).join("") + (results.length > 15 ? `<div style="font-size:.7rem;color:#4c567a;text-align:center;padding:6px">...ещё ${results.length-15} запросов в отчёте</div>` : "");
}

window.genPDF = async function() {
  if (!ckResults.length) return;
  const state = await chrome.runtime.sendMessage({ type: "GET_CHECKUP_STATE" });
  const { jsPDF: PDF } = window.jspdf;
  const doc = new PDF();
  let y = 20;
  doc.setFontSize(16); doc.text("AM Hub — Search Quality Checkup", 14, y); y += 8;
  doc.setFontSize(10); doc.setTextColor(100);
  doc.text(`Клиент: ${state.clientName||"—"} · ID: ${state.cabinetId||"—"} · ${new Date().toLocaleDateString("ru-RU")}`, 14, y); y += 6;
  doc.text(`Менеджер: ${state.managerName||"—"} · Режим: ${state.mode||"—"} · Запросов: ${ckResults.length}`, 14, y); y += 10;
  doc.setTextColor(0);
  ckResults.forEach((r, i) => {
    if (y > 270) { doc.addPage(); y = 20; }
    const sc = r.manualScore ?? r.autoScore;
    doc.setFontSize(11); doc.text(`${i+1}. [${sc}] ${r.query}`, 14, y); y += 6;
    if (r.reason) { doc.setFontSize(9); doc.setTextColor(80); doc.text(r.reason.slice(0,90), 18, y); y+=5; doc.setTextColor(0); }
    (r.recommendation||[]).slice(0,2).forEach(rec => {
      doc.setFontSize(8); doc.setTextColor(60); doc.text("• " + rec.slice(0,85), 18, y); y+=4;
    });
    if (r.aiRecommendation) { doc.setTextColor(100,50,200); doc.text("🤖 " + r.aiRecommendation.slice(0,85), 18, y); y+=4; doc.setTextColor(0); }
    y += 2;
  });
  doc.save(`checkup_${state.clientName||state.cabinetId}_${new Date().toISOString().slice(0,10)}.pdf`);
};

window.exportCSV = async function() {
  if (!ckResults.length) return;
  const state = await chrome.runtime.sendMessage({ type: "GET_CHECKUP_STATE" });
  const rows = [["#","Запрос","Показов","Оценка","Всего товаров","Продукт","Причина","Рекомендация","AI"]];
  ckResults.forEach(r => {
    rows.push([
      r.index, r.query, r.impressions||"", r.manualScore??r.autoScore,
      r.total||"", r.product||"", r.reason||"",
      (r.recommendation||[]).join("; "), r.aiRecommendation||""
    ]);
  });
  const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g,'""')}"`).join(",")).join("\n");
  const blob = new Blob(["\uFEFF"+csv], { type: "text/csv;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `checkup_${state.clientName||"export"}_${new Date().toISOString().slice(0,10)}.csv`;
  a.click(); URL.revokeObjectURL(url);
};

// ── Test MR ───────────────────────────────────────────────────────────────────
window.testMR = async function() {
  showBox("s-mr-result", "⏳ Проверяем...", "warn");
  const res = await chrome.runtime.sendMessage({ type: "SYNC_NOW" });
  showBox("s-mr-result", res.ok ? `✅ OK · ${res.result?.clients_synced||0} клиентов` : "❌ " + (res.error||"Ошибка"), res.ok ? "ok" : "err");
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function showAlert(msg, type) {
  const el = document.getElementById("ck-alert");
  if (!msg) { el.classList.add("hidden"); return; }
  el.className = "box box-" + type;
  el.textContent = msg;
  el.classList.remove("hidden");
}

function showBox(id, msg, type) {
  const el = document.getElementById(id);
  if (!msg) { el.classList.add("hidden"); return; }
  el.className = "box box-" + type;
  el.textContent = msg;
  el.classList.remove("hidden");
}

init();
