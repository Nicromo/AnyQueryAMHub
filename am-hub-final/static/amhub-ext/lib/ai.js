/**
 * ai.js — ИИ-рекомендации через Groq (primary) + Cloudflare (fallback)
 * 
 * Вызывается ТОЛЬКО для оценок ≤ AI_MAX_SCORE (по умолчанию 1 и 2).
 * Отправляет компактный контекст (~200 токенов) → получает рекомендацию.
 */

import { CONFIG } from "./config.js";

// ============================================================
// Промпт для ИИ
// ============================================================

function buildPrompt(query, score, details, flags, meta) {
  const products = details.slice(0, 10).map((d, i) =>
    `${i + 1}. "${d.name}" | Категория: ${d.category} | Релевантен: ${d.relevant ? "да" : "нет"}${d.attrMatch ? " (совпадение по атрибутам)" : ""}`
  ).join("\n");

  const flagsText = flags.length
    ? `\nФлаги: ${flags.map(f => f.message).join("; ")}`
    : "";

  return `Ты — эксперт по настройке e-commerce поиска (Diginetica). Проанализируй выдачу и дай конкретные рекомендации по настройке.

Запрос пользователя: «${query}»
Автооценка: ${score}/3
Найдено товаров: ${meta.total}${flagsText}

Топ-10 товаров в выдаче:
${products}

Доступные инструменты настройки:
- Синонимы (добавить синоним к запросу)
- Мерч-правила (буст/пин/исключение товаров или категорий)
- Редиректы (перенаправить запрос на категорию)
- Стоп-слова (исключить слова из обработки)
- Удаление сопутки (убрать товары из сторонних категорий)

Дай 1-3 конкретных рекомендации. По каждой укажи:
1. Какой инструмент использовать
2. Что именно сделать (конкретное действие)
3. Почему это поможет

Отвечай кратко, по делу, на русском. Формат JSON:
[{"tool": "...", "action": "...", "why": "..."}]

Только JSON, без markdown.`;
}

// ============================================================
// Groq API (primary)
// ============================================================

async function callGroq(prompt) {
  if (!CONFIG.GROQ_API_KEY) return null;

  try {
    const resp = await fetch(CONFIG.GROQ_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${CONFIG.GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: CONFIG.GROQ_MODEL,
        messages: [{ role: "user", content: prompt }],
        temperature: 0.3,
        max_tokens: 500,
      }),
    });

    if (!resp.ok) {
      console.warn(`Groq error: ${resp.status}`);
      return null;
    }

    const data = await resp.json();
    const text = data.choices?.[0]?.message?.content || "";
    return parseAiResponse(text);
  } catch (e) {
    console.warn("Groq failed:", e.message);
    return null;
  }
}

// ============================================================
// Cloudflare Workers AI (fallback)
// ============================================================

async function callCloudflare(prompt) {
  if (!CONFIG.CF_ACCOUNT_ID || !CONFIG.CF_API_TOKEN) return null;

  try {
    const url = `https://api.cloudflare.com/client/v4/accounts/${CONFIG.CF_ACCOUNT_ID}/ai/run/${CONFIG.CF_MODEL}`;
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${CONFIG.CF_API_TOKEN}`,
      },
      body: JSON.stringify({
        messages: [{ role: "user", content: prompt }],
        max_tokens: 500,
      }),
    });

    if (!resp.ok) {
      console.warn(`Cloudflare error: ${resp.status}`);
      return null;
    }

    const data = await resp.json();
    const text = data.result?.response || "";
    return parseAiResponse(text);
  } catch (e) {
    console.warn("Cloudflare failed:", e.message);
    return null;
  }
}

// ============================================================
// Парсинг ответа ИИ
// ============================================================

function parseAiResponse(text) {
  try {
    const clean = text.replace(/```json\s*/g, "").replace(/```/g, "").trim();
    const parsed = JSON.parse(clean);
    if (Array.isArray(parsed)) {
      return parsed.map(item => ({
        action: item.action || item.tool || "—",
        detail: item.why || item.action || "—",
        tool: item.tool || "—",
        source: "ai",
        priority: "high",
      }));
    }
  } catch (e) {
    // Если не JSON — попробуем извлечь текст
    if (text.length > 20) {
      return [{
        action: "ИИ-рекомендация",
        detail: text.slice(0, 500),
        tool: "—",
        source: "ai",
        priority: "medium",
      }];
    }
  }
  return null;
}

// ============================================================
// Основная функция: Groq → Cloudflare fallback
// ============================================================

/**
 * Получить ИИ-рекомендации для проблемного запроса
 * @param {string} query — поисковый запрос
 * @param {number} score — автооценка 0-3
 * @param {Array} details — анализ товаров
 * @param {Array} flags — флаги (мерч-правила и т.д.)
 * @param {Object} meta — мета-данные ответа API
 * @returns {Array|null} — массив рекомендаций или null
 */
export async function getAiRecommendations(query, score, details, flags, meta) {
  if (!CONFIG.AI_ENABLED) return null;
  if (score > CONFIG.AI_MAX_SCORE) return null;

  const prompt = buildPrompt(query, score, details, flags, meta);

  // Try Groq first
  const groqResult = await callGroq(prompt);
  if (groqResult) return groqResult;

  // Fallback to Cloudflare
  const cfResult = await callCloudflare(prompt);
  if (cfResult) return cfResult;

  // Both failed — return null, heuristics will be used
  return null;
}
