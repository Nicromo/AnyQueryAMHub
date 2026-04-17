/**
 * popup.js — UI logic
 */

import { generateReport, downloadReport } from "../lib/report.js";

const E = { 0: "🔴", 1: "🟠", 2: "🟡", 3: "🟢" };
const L = { 0: "Нулевая", 1: "Нерелев.", 2: "Вопросы", 3: "Отлично" };
const CLR = { 0: "#FEF2F2;#DC2626", 1: "#FFFBEB;#D97706", 2: "#EFF6FF;#2563EB", 3: "#F0FDF4;#16A34A" };

// DOM
const $ = id => document.getElementById(id);
const $cab = $("in-cabinet"), $key = $("in-apikey"), $mgr = $("in-manager"), $cli = $("in-client");
const $queries = $("in-queries"), $run = $("btn-run"), $cal = $("btn-cal");
const $pdf = $("btn-pdf"), $csv = $("btn-csv");
const $status = $("s-status"), $results = $("s-results");
const $mode = $("d-mode"), $alert = $("d-alert"), $prog = $("d-prog"), $progF = $("prog-f"), $progT = $("prog-t");
const $stats = $("d-stats"), $list = $("d-results");

let results = [], queryType = "top";

// ============================================================
// Query type tabs
// ============================================================

document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("act"));
    tab.classList.add("act");
    queryType = tab.dataset.type;
  };
});

// ============================================================
// Load cabinet
// ============================================================

$cab.addEventListener("change", async () => {
  const id = $cab.value.trim();
  if (!id) return;

  const resp = await msg({ type: "LOAD_CABINET", cabinetId: id });
  if (resp?.ok) {
    $key.value = resp.apiKey || "";
    $cli.value = resp.clientName || "";
    showAlert(`Кабинет загружен. Мерч-правил: ${resp.merchRulesCount || 0}`, "ok");
  } else if (resp?.error?.includes("CONFIGURE_API")) {
    showAlert("Бэкенд не настроен — введите API Key вручную", "warn");
  } else {
    showAlert(resp?.error || "Ошибка загрузки кабинета", "err");
  }
});

// ============================================================
// Run check
// ============================================================

$run.onclick = async () => {
  const apiKey = $key.value.trim();
  const raw = $queries.value.trim();
  if (!apiKey) { showAlert("Введите API Key", "err"); return; }
  if (!raw) { showAlert("Введите запросы", "err"); return; }

  const queries = raw.split("\n").map(q => q.trim()).filter(Boolean);
  await msg({ type: "SET_API_KEY_MANUAL", apiKey, siteUrl: null, clientName: $cli.value.trim() });
  await msg({ type: "SET_QUERIES", queries });
  if ($mgr.value.trim()) {
    await msg({ type: "SET_MANAGER_NAME", name: $mgr.value.trim() });
    chrome.storage.local.set({ managerName: $mgr.value.trim() });
  }

  $status.style.display = "";
  $prog.style.display = "";
  $run.disabled = true;
  $run.textContent = "⏳ Проверяю...";

  const resp = await msg({ type: "RUN_CHECK" });

  $run.disabled = false;
  $run.textContent = "▶ Проверить";
  $prog.style.display = "none";

  if (resp?.ok) {
    results = resp.results;
    render(results);
  } else {
    showAlert(resp?.error || "Ошибка", "err");
  }
};

// ============================================================
// Calibration
// ============================================================

$cal.onclick = async () => {
  const apiKey = $key.value.trim();
  const raw = $queries.value.trim();
  if (!apiKey || !raw) { showAlert("Введите API Key и запросы", "err"); return; }

  const queries = raw.split("\n").map(q => q.trim()).filter(Boolean);
  await msg({ type: "SET_API_KEY_MANUAL", apiKey });
  await msg({ type: "SET_QUERIES", queries });

  $status.style.display = "";
  showAlert("Калибрую...", "info");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const resp = await msg({ type: "RUN_CALIBRATION", tabId: tab.id });

  if (resp?.ok) {
    if (resp.mode === "api") {
      showMode("api");
      showAlert("✅ Выдача совпадает — API-режим", "ok");
    } else if (resp.mode === "site") {
      showMode("site");
      showAlert("⚠️ Расхождение — клиент ранжирует сам", "warn");
    } else if (resp.needSelector) {
      showAlert("Не удалось найти товары. Кликните на карточку.", "warn");
      chrome.tabs.sendMessage(tab.id, { type: "START_PICKER" });
    }
  } else {
    showAlert(resp?.error || "Ошибка", "err");
  }
};

// ============================================================
// PDF
// ============================================================

$pdf.onclick = () => {
  if (!results.length) return;
  try {
    const doc = generateReport({
      clientName: $cli.value.trim() || $cab.value.trim(),
      cabinetId: $cab.value.trim(),
      managerName: $mgr.value.trim(),
      mode: "api",
      results,
      merchRules: [],
    });
    downloadReport(doc, $cli.value.trim(), $cab.value.trim());
    showAlert("PDF скачан", "ok");
  } catch (e) {
    showAlert(`Ошибка PDF: ${e.message}`, "err");
  }
};

// ============================================================
// CSV
// ============================================================

$csv.onclick = () => {
  if (!results.length) return;
  const hdr = "№;Запрос;Товаров;Автооценка;Ручная;Проблема;Рекомендации;ИИ-рекомендации\n";
  const rows = results.map(r => {
    const recs = (r.recommendation || []).map(rc => rc.action).join(" | ");
    const aiRecs = (r.aiRecommendation || []).map(rc => `[${rc.tool}] ${rc.action}`).join(" | ");
    return `${r.index};${r.query};${r.total};${r.autoScore};${r.manualScore ?? ""};${esc(r.reason)};${esc(recs)};${esc(aiRecs)}`;
  }).join("\n");

  const blob = new Blob(["\uFEFF" + hdr + rows], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `checkup_${$cab.value.trim() || "export"}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
};

// ============================================================
// Progress listener
// ============================================================

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "PROGRESS") {
    const pct = Math.round((m.current / m.total) * 100);
    $progF.style.width = pct + "%";
    $progT.textContent = `${m.current} / ${m.total}`;
  }
  if (m.type === "SELECTOR_PICKED") {
    showAlert(`✅ Селектор: ${m.selector} (${m.count} карточек)`, "ok");
  }
});

// ============================================================
// Render
// ============================================================

function render(res) {
  $results.style.display = "";

  // Stats
  const cnt = { 0: 0, 1: 0, 2: 0, 3: 0 };
  res.forEach(r => cnt[r.autoScore]++);
  const avg = res.length ? (res.reduce((s, r) => s + r.autoScore, 0) / res.length).toFixed(1) : "—";

  $stats.innerHTML = [0, 1, 2, 3].map(s => {
    const [bg, fg] = CLR[s].split(";");
    const pct = res.length ? Math.round(cnt[s] / res.length * 100) : 0;
    return `<div class="sc" style="background:${bg};color:${fg}"><div class="n">${cnt[s]}</div><div class="l">${E[s]} ${L[s]} ${pct}%</div></div>`;
  }).join("") + `<div class="sc" style="background:#0F172A;color:#fff"><div class="n">${avg}</div><div class="l">Средняя</div></div>`;

  // Results
  $list.innerHTML = res.map(r => {
    const flags = (r.flags || []).map(f => `<span class="flag">⚠ ${esc(f.message)}</span>`).join("");

    // Heuristic recs
    const hRecs = (r.recommendation || []).filter(rc => rc.priority !== "none").map(rc =>
      `<div class="ri-rec"><b>⚙ ${esc(rc.action)}</b> — ${esc(rc.detail)}</div>`
    ).join("");

    // AI recs
    const aiRecs = (r.aiRecommendation || []).map(rc =>
      `<div class="ri-rec"><span class="ai">🤖 [${esc(rc.tool)}]</span> <b>${esc(rc.action)}</b> — ${esc(rc.detail || rc.why || "")}</div>`
    ).join("");

    const recsBlock = (hRecs || aiRecs)
      ? `<div class="ri-recs">${hRecs}${aiRecs}</div>`
      : "";

    return `
      <div class="ri">
        <div class="ri-top">
          <span class="ri-idx">${r.index}</span>
          <span class="ri-q">${esc(r.query)}</span>
          <span style="color:#94A3B8;font-size:10px">${r.total}</span>
          <span class="badge b${r.autoScore}">${E[r.autoScore]} ${r.autoScore}/3</span>
        </div>
        <div class="ri-reason">${esc(r.reason)}</div>
        ${flags}
        ${recsBlock}
      </div>
    `;
  }).join("");
}

// ============================================================
// Helpers
// ============================================================

function msg(data) { return chrome.runtime.sendMessage(data); }
function showAlert(txt, type) {
  $status.style.display = "";
  $alert.innerHTML = `<div class="alert a-${type}">${txt}</div>`;
}
function showMode(mode) {
  $mode.innerHTML = `<span class="mode-b mode-${mode}">${mode === "api" ? "⚡ API" : "🌐 Сайт"}</span>`;
}
function esc(s) { const d = document.createElement("div"); d.textContent = s || ""; return d.innerHTML; }

// ============================================================
// Restore state
// ============================================================

chrome.storage.local.get("managerName", d => { if (d.managerName) $mgr.value = d.managerName; });
msg({ type: "GET_STATE" }).then(s => {
  if (!s) return;
  if (s.apiKey) $key.value = s.apiKey;
  if (s.clientName) $cli.value = s.clientName;
  if (s.cabinetId) $cab.value = s.cabinetId;
  if (s.results?.length) { results = s.results; $status.style.display = ""; if (s.mode) showMode(s.mode); render(s.results); }
});
