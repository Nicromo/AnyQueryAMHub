"""
AI Assistant — генерация prep brief, followup, анализ рисков.
Использует Groq API или Qwen (DashScope, бесплатный tier) как fallback.
"""
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ", "")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")


# ── Бизнес-контекст для AI ────────────────────────────────────────────────
# Все промпты оборачиваем в этот system-промпт, чтобы модель понимала домен.
DOMAIN_CONTEXT = """Ты — AI-ассистент Account Manager'а в AnyQuery.

О продукте AnyQuery:
• AnyQuery — B2B-платформа умного поиска и рекомендаций для крупных e-com (Yves Rocher, Lazurit, Vodovoz, Kuvalda, Mechta.kz, Beeline и др.).
• Продукт включает: поиск (Search), рекомендации (Recsys), аналитику, антифрод, сортировки.
• Клиенты интегрируют AnyQuery как SaaS: заводят фиды товаров, настраивают мерч-правила, получают API для поиска/реков.

Ключевые понятия:
• Merchrules (мерч-правила) — бизнес-правила для поисковой выдачи:
  поднять/опустить товары, перемешать по брендам, фиксированные позиции. Настраиваются в merchrules.any-platform.ru.
• Синонимы — словарь: запрос «крем от морщин» ⇄ «антивозрастной крем».
• Whitelist — список разрешённых для показа товаров / выдач.
• Фид (feed) — XML/JSON товаров партнёра, регулярно синхронизируется.
• Diginetica — внешний поисковый движок, который мы анализируем на Top-50 / чекапах.
• NDCG@20, Precision@20, Конверсия — ключевые качественные метрики поиска клиента.
• Чекап — регулярная проверка качества поиска: берём ~20 реальных запросов, смотрим выдачу, ставим оценки.
• QBR (Quarterly Business Review) — квартальная встреча с клиентом, ревью результатов + план.
• Сегменты клиентов: ENT (ключевые, >1млн выручки), SME+/SME/SME-, SMB, SS (sandbox).
• Health Score — 0..1, интегральный показатель здоровья клиента (задачи, встречи, метрики, churn-риск).
• Роадмап — квартальный план развития клиента (Q1/Q2/Q3/Q4 + Бэклог).
• Top-50 — приоритетный список 50 ключевых клиентов с ежемесячными метриками.
• MRR / GMV — monthly recurring revenue / общий оборот клиента на платформе.
• Time (Тайм) — внутренний Mattermost tbank'а, где клиенты оставляют тикеты в каналах.
• Ktalk — Контур.Толк, корп. видеоконференции для встреч.

Твоя задача: понимать, что Account Manager работает с конкретным партнёром,
видит его задачи, встречи, метрики и мерч-правила; давать точные,
прикладные рекомендации на русском, без воды. Не выдумывай факты — если
данных мало, так и напиши.
"""


def _chat_sync(system: str, user: str, max_tokens: int = 3000) -> str:
    """Синхронный запрос к AI (Groq → Qwen fallback)."""
    # Попробовать Groq
    if GROQ_API_KEY:
        import httpx
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "llama-3.3-70b-versatile", "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Groq failed, trying Qwen: {e}")

    # Fallback на Qwen (DashScope — 1M токенов/мес бесплатно)
    if QWEN_API_KEY:
        import httpx
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={"model": "qwen-plus", "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Qwen failed: {e}")

    raise RuntimeError("No AI provider available (set GROQ_API_KEY or QWEN_API_KEY)")


def generate_prep_brief(client, tasks: list, meetings: list) -> str:
    """Генерация подготовки к встрече."""
    tasks_text = "\n".join([f"- {t.title}" for t in tasks[:10]]) if tasks else "Нет открытых задач"
    meetings_text = "\n".join([f"- {m.title or m.type} ({m.date.strftime('%d.%m.%Y') if m.date else ''})" for m in meetings[:5]]) if meetings else "Нет прошлых встреч"

    prompt = f"""Клиент: {client.name} ({client.segment or '—'})

Открытые задачи:
{tasks_text}

Прошлые встречи:
{meetings_text}

Сделай краткую подготовку к встрече (3-5 пунктов):
1. Контекст и текущий статус
2. Ключевые вопросы для обсуждения
3. Рекомендуемые действия"""

    try:
        return _chat_sync(DOMAIN_CONTEXT, prompt, max_tokens=1000)
    except Exception:
        return f"📋 Подготовка к встрече: {client.name}\n\nЗадачи: {len(tasks)}\nВстречи: {len(meetings)}"


def generate_smart_followup(client, tasks: list, meetings: list) -> str:
    """Генерация фолоуапа после встречи."""
    tasks_list = "\n".join([f"- {t.title}" for t in tasks[:5]]) if tasks else "—"
    meetings_list = "\n".join([f"- {m.title or m.type}" for m in meetings[:3]]) if meetings else "—"

    prompt = f"""Сделай итоговый фолоуап после встречи с {client.name}.

Открытые задачи:
{tasks_list}

Последние встречи:
{meetings_list}

Ответь в формате:
**Итоги встречи с {client.name}**

**Обсудили:**
1. ...
2. ...

**Дальнейшие шаги:**
- Мы: ...
- С вашей стороны: ..."""

    try:
        return _chat_sync(DOMAIN_CONTEXT, prompt, max_tokens=1000)
    except Exception:
        return f"✍️ Фолоуап: {client.name}\n\nЗадач: {len(tasks)}\nВстреч: {len(meetings)}"


def detect_account_risks(client, tasks: list, meetings: list) -> list:
    """Обнаружение рисков по аккаунту."""
    risks = []
    now = datetime.now()

    if tasks:
        blocked = [t for t in tasks if t.status == "blocked"]
        if blocked:
            risks.append(f"🔴 {len(blocked)} заблокированных задач")

        overdue = [t for t in tasks if t.due_date and t.due_date < now and t.status in ("plan", "in_progress")]
        if overdue:
            risks.append(f"⚠️ {len(overdue)} просроченных задач")

    if meetings:
        last = max((m.date for m in meetings if m.date), default=None)
        if last and (now - last).days > 30:
            risks.append(f"📅 Нет встреч {(now - last).days} дней")

    if client.health_score and client.health_score < 0.5:
        risks.append(f"📉 Низкий health score: {client.health_score:.0%}")

    return risks or ["✅ Рисков не обнаружено"]
