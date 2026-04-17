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


def _chat_sync(system: str, user: str, max_tokens: int = 3000, user_groq_key: str = "") -> str:
    """Синхронный запрос к AI (Groq → Qwen fallback)."""
    # Попробовать Groq
    # Per-user key overrides global
    groq_key = user_groq_key or GROQ_API_KEY
    if groq_key:
        import httpx
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": "llama-3.3-70b-versatile", "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}
                    ], "max_tokens": max_tokens, "temperature": 0.1},
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
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
        return _chat_sync("", prompt, max_tokens=1000)
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
        return _chat_sync("", prompt, max_tokens=1000)
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
