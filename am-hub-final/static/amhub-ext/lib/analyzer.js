/**
 * analyzer.js — Анализ релевантности + эвристические рекомендации
 */

import { CONFIG } from "./config.js";

// ============================================================
// Стемминг (упрощённый русский)
// ============================================================

function stem(word) {
  const w = word.toLowerCase().replace(/ё/g, "е");
  if (w.length <= 3) return w;
  return w.replace(
    /(ами|ями|ов|ев|ей|ий|ый|ой|ая|яя|ое|ее|ие|ые|ом|ем|ах|ях|ую|юю|ых|их|ам|ям|ою|ею|у|а|о|ы|и|е|я|ь|й|ка|ки|ку|ке|ок)$/,
    ""
  );
}

// ============================================================
// Анализ одного товара
// ============================================================

function analyzeProduct(product, queryStems, queryRaw) {
  const name = (product.name || "").toLowerCase();
  const stemmedName = (product.stemmedName || "").toLowerCase();
  const catValues = product.categories ? Object.values(product.categories).map(c => c.toLowerCase()) : [];
  const catText = catValues.join(" ");
  const attrEntries = product.attributes || {};
  const attrValues = Object.entries(attrEntries).flatMap(([k, v]) =>
    Array.isArray(v) ? v.map(x => String(x).toLowerCase()) : [String(v).toLowerCase()]
  );
  const attrText = attrValues.join(" ");

  let nameMatch = false, stemMatch = false, catMatch = false, attrMatch = false;

  for (const qs of queryStems) {
    if (name.includes(qs) || name.includes(queryRaw)) nameMatch = true;
    if (stemmedName.includes(qs)) stemMatch = true;
    if (catText.includes(qs) || catText.includes(queryRaw)) catMatch = true;
    if (attrText.includes(qs) || attrText.includes(queryRaw)) attrMatch = true;
  }

  const relevant = nameMatch || stemMatch || catMatch;
  const partiallyRelevant = attrMatch && !relevant;

  return {
    id: product.id,
    name: product.name,
    category: catValues[0] || "—",
    categories: catValues,
    attributes: attrEntries,
    relevant,
    partiallyRelevant,
    nameMatch, stemMatch, catMatch, attrMatch,
    available: product.available,
    price: product.skus?.[0]?.price || "—",
    image: product.skus?.[0]?.image_url || "",
    url: product.skus?.[0]?.link_url || "",
  };
}

// ============================================================
// Анализ всей выдачи
// ============================================================

export function analyzeQuery(query, apiResponse, merchRules = []) {
  const { products, total, zeroQueries, redirectUrl, correction, affectedByRule } = apiResponse;

  const meta = { total, zeroQueries, redirectUrl, correction, affectedByRule };

  // Оценка 0: нулевая выдача
  if (total === 0 || !products || products.length === 0 || zeroQueries) {
    return {
      score: 0,
      reason: "Нулевая выдача — товары не найдены",
      recommendation: generateRecommendation(0, { query, meta, details: [], merchRules }),
      details: [],
      flags: checkFlags(query, meta, merchRules),
      meta,
    };
  }

  const queryRaw = query.toLowerCase();
  const queryWords = queryRaw.split(/\s+/).filter(w => w.length > 2);
  const queryStems = queryWords.map(stem);

  const topN = products.slice(0, CONFIG.TOP_N_PRODUCTS);
  const details = topN.map((p, idx) => ({
    position: idx + 1,
    ...analyzeProduct(p, queryStems, queryRaw),
  }));

  // Все недоступны
  if (topN.every(p => !p.available)) {
    return {
      score: 0,
      reason: "Все товары в выдаче недоступны (распродано)",
      recommendation: generateRecommendation(0, { query, meta, details, merchRules, allUnavailable: true }),
      details,
      flags: checkFlags(query, meta, merchRules),
      meta,
    };
  }

  const relevantCount = details.filter(d => d.relevant).length;
  const totalAnalyzed = details.length;
  const relevantRatio = relevantCount / totalAnalyzed;

  let score, reason;

  if (relevantRatio >= CONFIG.SCORE_3_THRESHOLD) {
    const topIrrelevant = details.slice(0, 3).filter(d => !d.relevant);
    if (topIrrelevant.length > 0) {
      score = 2;
      const names = topIrrelevant.map(d => `«${d.name}»`).join(", ");
      reason = `Товары релевантны (${relevantCount}/${totalAnalyzed}), но в топ-3 нерелевантные: ${names}`;
    } else {
      score = 3;
      reason = `Выдача релевантна: ${relevantCount}/${totalAnalyzed} соответствуют запросу`;
    }
  } else if (relevantRatio >= CONFIG.SCORE_2_THRESHOLD) {
    const irrelevant = details.filter(d => !d.relevant).slice(0, 3);
    const names = irrelevant.map(d => `«${d.name}»`).join(", ");
    const foreignCats = [...new Set(irrelevant.map(d => d.category).filter(c => c !== "—"))];
    score = 2;
    reason = `Частично релевантная выдача (${relevantCount}/${totalAnalyzed}). Нерелевантные: ${names}`;
    if (foreignCats.length) reason += `. Категории-примеси: ${foreignCats.join(", ")}`;
  } else if (relevantCount > 0) {
    score = 1;
    const bad = details.filter(d => !d.relevant).slice(0, 3).map(d => `«${d.name}»`).join(", ");
    reason = `Преимущественно нерелевантная выдача (${relevantCount}/${totalAnalyzed}). Примеры: ${bad}`;
  } else {
    score = 1;
    const topNames = details.slice(0, 3).map(d => `«${d.name}»`).join(", ");
    const topCats = [...new Set(details.map(d => d.category).filter(c => c !== "—"))].slice(0, 3);
    reason = `Ни один товар не соответствует запросу «${query}». Выдача: ${topNames}. Категории: ${topCats.join(", ")}`;
  }

  const flags = checkFlags(query, meta, merchRules);
  if (flags.some(f => f.type === "merch_rule") && score < 3) {
    reason += ". ⚠️ Применено мерч-правило клиента";
  }

  const context = { query, meta, details, merchRules, relevantCount, totalAnalyzed, relevantRatio, score };

  return {
    score,
    reason,
    recommendation: generateRecommendation(score, context),
    details,
    flags,
    meta,
  };
}

// ============================================================
// Флаги
// ============================================================

function checkFlags(query, meta, merchRules) {
  const flags = [];

  if (meta.affectedByRule) {
    flags.push({ type: "merch_rule_active", message: "Активно мерч-правило клиента" });
  }

  if (meta.redirectUrl) {
    flags.push({ type: "redirect", message: `Редирект на: ${meta.redirectUrl}` });
  }

  if (meta.correction) {
    flags.push({ type: "correction", message: `Автокоррекция: «${meta.correction}»` });
  }

  const matchedRule = merchRules.find(r => {
    const rq = (r.query || "").toLowerCase();
    const rqs = (r.queries || []).map(q => q.toLowerCase());
    return rq === query.toLowerCase() || rqs.includes(query.toLowerCase());
  });
  if (matchedRule) {
    flags.push({
      type: "merch_rule",
      message: `Мерч-правило: ${matchedRule.name || matchedRule.id || "без названия"}`,
      rule: matchedRule,
    });
  }

  return flags;
}

// ============================================================
// Эвристические рекомендации (слой 1 — без ИИ)
// ============================================================

function generateRecommendation(score, ctx) {
  const recs = [];

  if (score === 0) {
    if (ctx.allUnavailable) {
      recs.push({
        action: "Проверить наличие товаров",
        detail: "Все товары в выдаче имеют статус «нет в наличии». Проверить каталог и наличие на складе.",
        priority: "high",
      });
    } else if (ctx.meta?.correction) {
      recs.push({
        action: "Добавить синонимы",
        detail: `Запрос «${ctx.query}» дал нулевую выдачу даже с автокоррекцией на «${ctx.meta.correction}». Добавить синоним «${ctx.query}» в настройках поиска.`,
        priority: "high",
      });
    } else if (ctx.meta?.redirectUrl) {
      recs.push({
        action: "Проверить редирект",
        detail: `Настроен редирект на ${ctx.meta.redirectUrl}. Убедиться, что страница назначения содержит релевантные товары.`,
        priority: "medium",
      });
    } else {
      recs.push({
        action: "Добавить синонимы или проверить каталог",
        detail: `Запрос «${ctx.query}» — нулевая выдача. Вариант 1: добавить синонимы. Вариант 2: проверить, есть ли такие товары в каталоге. Вариант 3: настроить редирект на ближайшую категорию.`,
        priority: "high",
      });
    }
    return recs;
  }

  if (score === 1) {
    // Определяем категории-примеси
    const foreignCats = [...new Set(
      (ctx.details || []).filter(d => !d.relevant).map(d => d.category).filter(c => c !== "—")
    )];

    if (foreignCats.length > 0) {
      recs.push({
        action: "Удалить сопутствующие категории из выдачи",
        detail: `В выдаче по «${ctx.query}» товары из нерелевантных категорий: ${foreignCats.join(", ")}. Рассмотреть исключение этих категорий через мерч-правило.`,
        priority: "high",
      });
    }

    if (ctx.meta?.affectedByRule) {
      recs.push({
        action: "Пересмотреть мерч-правило",
        detail: `По запросу «${ctx.query}» активно мерч-правило, которое ухудшает выдачу. Рекомендуется проверить и скорректировать правило.`,
        priority: "high",
      });
    }

    recs.push({
      action: "Проверить маппинг запроса",
      detail: `Запрос «${ctx.query}» не матчится с товарами в каталоге. Рассмотреть: добавить синонимы, настроить буст релевантной категории, или создать мерч-правило для этого запроса.`,
      priority: "medium",
    });

    return recs;
  }

  if (score === 2) {
    // Проблема ранжирования vs примеси
    const topIrrelevant = (ctx.details || []).slice(0, 3).filter(d => !d.relevant);
    const bottomRelevant = (ctx.details || []).slice(3).filter(d => d.relevant);

    if (topIrrelevant.length > 0 && bottomRelevant.length > 0) {
      // Релевантные товары есть, но ниже нерелевантных
      const relevantCats = [...new Set(bottomRelevant.map(d => d.category).filter(c => c !== "—"))];
      recs.push({
        action: "Забустить релевантные товары",
        detail: `Релевантные товары есть, но находятся ниже нерелевантных. Рассмотреть буст товаров из категории: ${relevantCats.join(", ")}. Или понизить нерелевантные: ${topIrrelevant.map(d => `«${d.name}»`).join(", ")}.`,
        priority: "high",
      });
    }

    const foreignCats = [...new Set(
      (ctx.details || []).filter(d => !d.relevant).map(d => d.category).filter(c => c !== "—")
    )];
    if (foreignCats.length > 0) {
      recs.push({
        action: "Удалить сопутку",
        detail: `В выдаче по «${ctx.query}» есть товары из сторонних категорий: ${foreignCats.join(", ")}. Удалить их через исключение в мерч-правиле.`,
        priority: "medium",
      });
    }

    if (ctx.meta?.affectedByRule) {
      recs.push({
        action: "Проверить мерч-правило",
        detail: `Активно мерч-правило по этому запросу. Возможно, оно влияет на порядок товаров и вносит нерелевантные позиции.`,
        priority: "medium",
      });
    }

    return recs;
  }

  // score === 3
  return [{ action: "Без действий", detail: "Выдача релевантна, ранжирование корректное.", priority: "none" }];
}
