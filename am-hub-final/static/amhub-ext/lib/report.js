/**
 * report.js — Генерация PDF-отчёта
 * 
 * Использует jsPDF (загружается из CDN в popup).
 * Генерирует двуязычный отчёт с:
 * - Сводкой по клиенту
 * - Статистикой оценок
 * - Детальным разбором проблемных запросов
 * - Рекомендациями (эвристики + ИИ)
 * - Блоком мерч-правил
 */

const SCORE_LABELS = {
  0: "Нулевая выдача",
  1: "Нерелевантная выдача",
  2: "Есть вопросы",
  3: "Отлично",
};

const PRIORITY_LABELS = {
  high: "Высокий",
  medium: "Средний",
  low: "Низкий",
  none: "—",
};

/**
 * Сгенерировать PDF-отчёт
 * @param {Object} params
 * @param {string} params.clientName — название клиента
 * @param {string} params.cabinetId — ID кабинета
 * @param {string} params.managerName — имя менеджера
 * @param {string} params.mode — режим проверки (api/site)
 * @param {Array} params.results — массив результатов проверки
 * @param {Array} params.merchRules — мерч-правила клиента
 * @param {Object} params.previousCheckup — предыдущий чекап для сравнения (optional)
 */
export function generateReport({ clientName, cabinetId, managerName, mode, results, merchRules, previousCheckup }) {
  // jsPDF должен быть доступен глобально (загружен в popup.html)
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });

  const PAGE_W = 210;
  const MARGIN = 18;
  const CONTENT_W = PAGE_W - MARGIN * 2;
  let y = 0;

  // Цвета
  const COLORS = {
    dark: [15, 23, 42],
    gray: [100, 116, 139],
    lightGray: [226, 232, 240],
    white: [255, 255, 255],
    score0: [220, 38, 38],
    score1: [217, 119, 6],
    score2: [37, 99, 235],
    score3: [22, 163, 74],
    accent: [59, 130, 246],
  };

  function setColor(c) { doc.setTextColor(...c); }
  function setFillColor(c) { doc.setFillColor(...c); }

  function checkPage(needed = 30) {
    if (y + needed > 280) {
      doc.addPage();
      y = MARGIN;
    }
  }

  function drawLine() {
    doc.setDrawColor(...COLORS.lightGray);
    doc.line(MARGIN, y, PAGE_W - MARGIN, y);
    y += 4;
  }

  // ============================================================
  // Расчёт статистики
  // ============================================================

  const counts = { 0: 0, 1: 0, 2: 0, 3: 0 };
  results.forEach(r => counts[r.autoScore]++);
  const total = results.length;
  const avgScore = total ? (results.reduce((s, r) => s + r.autoScore, 0) / total) : 0;
  const problemQueries = results.filter(r => r.autoScore <= 2);

  // ============================================================
  // СТРАНИЦА 1: Шапка + сводка
  // ============================================================

  // Header bar
  setFillColor(COLORS.dark);
  doc.rect(0, 0, PAGE_W, 42, "F");

  doc.setFont("helvetica", "bold");
  doc.setFontSize(18);
  setColor(COLORS.white);
  doc.text("Search Quality Report", MARGIN, 18);

  doc.setFontSize(10);
  doc.setFont("helvetica", "normal");
  doc.text(`${clientName || "Клиент"} | Кабинет: ${cabinetId}`, MARGIN, 28);
  doc.text(`${new Date().toLocaleDateString("ru-RU")} | Менеджер: ${managerName || "—"} | Режим: ${mode === "api" ? "API" : "Сайт"}`, MARGIN, 35);

  y = 52;

  // Summary cards
  const cardW = CONTENT_W / 5;
  const scoreColors = [COLORS.score0, COLORS.score1, COLORS.score2, COLORS.score3];

  for (let s = 0; s <= 3; s++) {
    const x = MARGIN + s * cardW;
    setFillColor(scoreColors[s].map(c => Math.min(255, c + 180))); // lighter version
    doc.roundedRect(x + 1, y, cardW - 2, 28, 3, 3, "F");

    doc.setFontSize(20);
    doc.setFont("helvetica", "bold");
    setColor(scoreColors[s]);
    doc.text(String(counts[s]), x + cardW / 2, y + 13, { align: "center" });

    doc.setFontSize(7);
    doc.setFont("helvetica", "normal");
    doc.text(SCORE_LABELS[s], x + cardW / 2, y + 21, { align: "center" });

    doc.setFontSize(7);
    setColor(COLORS.gray);
    const pct = total ? Math.round(counts[s] / total * 100) : 0;
    doc.text(`${pct}%`, x + cardW / 2, y + 26, { align: "center" });
  }

  // Average score card
  const avgX = MARGIN + 4 * cardW;
  setFillColor(COLORS.dark);
  doc.roundedRect(avgX + 1, y, cardW - 2, 28, 3, 3, "F");
  doc.setFontSize(20);
  doc.setFont("helvetica", "bold");
  setColor(COLORS.white);
  doc.text(avgScore.toFixed(1), avgX + cardW / 2, y + 13, { align: "center" });
  doc.setFontSize(7);
  doc.text("Средняя", avgX + cardW / 2, y + 21, { align: "center" });
  doc.text(`${total} запросов`, avgX + cardW / 2, y + 26, { align: "center" });

  y += 36;

  // Comparison with previous
  if (previousCheckup) {
    const prevAvg = previousCheckup.avgScore || 0;
    const diff = avgScore - prevAvg;
    const trend = diff > 0 ? "▲" : diff < 0 ? "▼" : "=";
    const trendColor = diff > 0 ? COLORS.score3 : diff < 0 ? COLORS.score0 : COLORS.gray;

    setColor(trendColor);
    doc.setFontSize(10);
    doc.setFont("helvetica", "bold");
    doc.text(`${trend} ${diff > 0 ? "+" : ""}${diff.toFixed(1)} vs предыдущий чекап (${prevAvg.toFixed(1)})`, MARGIN, y);
    y += 8;
  }

  // Verdict
  let verdict;
  if (avgScore >= 2.5) verdict = "Выдача в хорошем состоянии. Точечные улучшения ниже.";
  else if (avgScore >= 1.5) verdict = "Есть заметные проблемы. Рекомендуется внести корректировки.";
  else verdict = "Критические проблемы с релевантностью. Требуется срочная настройка.";

  setColor(COLORS.dark);
  doc.setFontSize(11);
  doc.setFont("helvetica", "bold");
  doc.text("Резюме", MARGIN, y);
  y += 6;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  setColor(COLORS.gray);
  doc.text(verdict, MARGIN, y);
  y += 10;

  drawLine();

  // ============================================================
  // ПРОБЛЕМНЫЕ ЗАПРОСЫ — детали + рекомендации
  // ============================================================

  setColor(COLORS.dark);
  doc.setFontSize(13);
  doc.setFont("helvetica", "bold");
  doc.text(`Проблемные запросы (${problemQueries.length})`, MARGIN, y);
  y += 8;

  for (const result of problemQueries) {
    checkPage(50);

    // Query header
    const scoreColor = scoreColors[result.autoScore] || COLORS.gray;
    setFillColor(scoreColor.map(c => Math.min(255, c + 200)));
    doc.roundedRect(MARGIN, y - 4, CONTENT_W, 14, 2, 2, "F");

    doc.setFontSize(10);
    doc.setFont("helvetica", "bold");
    setColor(scoreColor);
    doc.text(`${result.autoScore}/3`, MARGIN + 3, y + 4);

    setColor(COLORS.dark);
    doc.text(`«${result.query}»`, MARGIN + 16, y + 4);

    setColor(COLORS.gray);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8);
    doc.text(`${result.total} товаров`, PAGE_W - MARGIN - 3, y + 4, { align: "right" });

    y += 14;

    // Reason
    doc.setFontSize(8);
    setColor(COLORS.gray);
    const reasonLines = doc.splitTextToSize(result.reason || "", CONTENT_W - 4);
    doc.text(reasonLines, MARGIN + 2, y);
    y += reasonLines.length * 4 + 2;

    // Flags
    if (result.flags && result.flags.length) {
      for (const flag of result.flags) {
        setColor(COLORS.score1);
        doc.setFontSize(7);
        doc.text(`⚠ ${flag.message}`, MARGIN + 2, y);
        y += 4;
      }
    }

    // Top products
    if (result.details && result.details.length > 0) {
      const top5 = result.details.slice(0, 5);
      doc.setFontSize(7);
      setColor(COLORS.gray);
      doc.text("Товары в выдаче:", MARGIN + 2, y);
      y += 4;

      for (const d of top5) {
        checkPage(6);
        const marker = d.relevant ? "✓" : "✗";
        const markerColor = d.relevant ? COLORS.score3 : COLORS.score0;
        setColor(markerColor);
        doc.text(marker, MARGIN + 4, y);
        setColor(COLORS.dark);
        doc.setFontSize(7);
        const productText = `${d.position}. ${d.name} — ${d.category}`;
        doc.text(doc.splitTextToSize(productText, CONTENT_W - 16)[0], MARGIN + 10, y);
        y += 4;
      }
      y += 2;
    }

    // Recommendations
    const allRecs = [...(result.recommendation || []), ...(result.aiRecommendation || [])];
    if (allRecs.length > 0) {
      checkPage(allRecs.length * 12 + 6);
      setColor(COLORS.accent);
      doc.setFontSize(8);
      doc.setFont("helvetica", "bold");
      doc.text("Рекомендации:", MARGIN + 2, y);
      y += 5;

      for (const rec of allRecs) {
        checkPage(12);
        const isAi = rec.source === "ai";
        const prefix = isAi ? "🤖" : "⚙";

        doc.setFont("helvetica", "bold");
        doc.setFontSize(8);
        setColor(COLORS.dark);
        const actionText = `${prefix} ${rec.tool ? `[${rec.tool}] ` : ""}${rec.action}`;
        doc.text(doc.splitTextToSize(actionText, CONTENT_W - 8)[0], MARGIN + 4, y);
        y += 4;

        doc.setFont("helvetica", "normal");
        doc.setFontSize(7);
        setColor(COLORS.gray);
        const detailLines = doc.splitTextToSize(rec.detail || rec.why || "", CONTENT_W - 10);
        doc.text(detailLines.slice(0, 3), MARGIN + 6, y);
        y += detailLines.slice(0, 3).length * 3.5 + 3;
      }
    }

    y += 4;
    drawLine();
  }

  // ============================================================
  // ВСЕ ЗАПРОСЫ — компактная таблица
  // ============================================================

  checkPage(40);
  setColor(COLORS.dark);
  doc.setFontSize(13);
  doc.setFont("helvetica", "bold");
  doc.text("Все запросы", MARGIN, y);
  y += 8;

  // Table header
  setFillColor([241, 245, 249]);
  doc.rect(MARGIN, y - 4, CONTENT_W, 8, "F");
  doc.setFontSize(7);
  doc.setFont("helvetica", "bold");
  setColor(COLORS.gray);
  doc.text("№", MARGIN + 2, y);
  doc.text("Запрос", MARGIN + 12, y);
  doc.text("Товаров", MARGIN + 90, y);
  doc.text("Оценка", MARGIN + 115, y);
  doc.text("Проблема", MARGIN + 132, y);
  y += 6;

  for (const r of results) {
    checkPage(6);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(7);

    setColor(COLORS.gray);
    doc.text(String(r.index), MARGIN + 2, y);

    setColor(COLORS.dark);
    doc.text(r.query.slice(0, 30), MARGIN + 12, y);

    setColor(COLORS.gray);
    doc.text(String(r.total), MARGIN + 90, y);

    const sc = scoreColors[r.autoScore];
    setColor(sc);
    doc.setFont("helvetica", "bold");
    doc.text(`${r.autoScore}/3`, MARGIN + 115, y);

    doc.setFont("helvetica", "normal");
    setColor(COLORS.gray);
    const shortReason = (r.reason || "").slice(0, 50) + ((r.reason || "").length > 50 ? "..." : "");
    doc.text(shortReason, MARGIN + 132, y);

    y += 5;
  }

  // ============================================================
  // МЕРЧ-ПРАВИЛА
  // ============================================================

  if (merchRules && merchRules.length > 0) {
    checkPage(30);
    y += 6;
    setColor(COLORS.dark);
    doc.setFontSize(11);
    doc.setFont("helvetica", "bold");
    doc.text(`Активные мерч-правила (${merchRules.length})`, MARGIN, y);
    y += 7;

    const affectedRuleIds = new Set();
    results.forEach(r => {
      (r.flags || []).forEach(f => {
        if (f.rule) affectedRuleIds.add(f.rule.id || f.rule.name);
      });
    });

    for (const rule of merchRules.slice(0, 15)) {
      checkPage(8);
      const affected = affectedRuleIds.has(rule.id) || affectedRuleIds.has(rule.name);
      doc.setFontSize(7);
      doc.setFont("helvetica", "normal");
      setColor(affected ? COLORS.score1 : COLORS.gray);
      const ruleText = `${affected ? "⚠ " : "  "}${rule.name || rule.id} — ${rule.query || (rule.queries || []).join(", ")}`;
      doc.text(doc.splitTextToSize(ruleText, CONTENT_W)[0], MARGIN + 2, y);
      y += 5;
    }
  }

  // ============================================================
  // Footer
  // ============================================================

  const pageCount = doc.internal.getNumberOfPages();
  for (let i = 1; i <= pageCount; i++) {
    doc.setPage(i);
    doc.setFontSize(7);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(148, 163, 184);
    doc.text(
      `Search Quality Checkup | ${clientName || cabinetId} | ${new Date().toLocaleDateString("ru-RU")} | Стр. ${i}/${pageCount}`,
      PAGE_W / 2, 290, { align: "center" }
    );
  }

  return doc;
}

/**
 * Скачать PDF
 */
export function downloadReport(doc, clientName, cabinetId) {
  const date = new Date().toISOString().slice(0, 10);
  const name = clientName ? clientName.replace(/[^a-zA-Zа-яА-Я0-9]/g, "_") : cabinetId;
  doc.save(`checkup_${name}_${date}.pdf`);
}
