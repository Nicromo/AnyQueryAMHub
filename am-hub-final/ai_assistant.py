"""
AI-ассистент для генерации фолоуапов, подготовки и детекции рисков.
Используется Groq API для обработки данных встреч и задач.
"""
import json
import logging
import os
from datetime import datetime, date

import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", os.getenv("API_GROQ", ""))
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def generate_smart_followup(client: dict, meetings: list, tasks: list) -> str:
    """
    Генерирует текст фолоуапа на основе последних встреч и задач.
    Возвращает текст на русском языке для отправки клиенту.
    """
    if not GROQ_API_KEY:
        return "Фолоуап: спасибо за встречу, продолжаем работу по обсуждённым пунктам."

    # Подготавливаем контекст
    meetings_text = ""
    if meetings:
        for m in meetings[:3]:
            meetings_text += f"• {m.get('meeting_date')}: {m.get('summary', '')}\n"

    tasks_text = ""
    if tasks:
        for t in tasks[:5]:
            status_str = "открыта" if t.get("status") == "open" else t.get("status", "открыта")
            tasks_text += f"• {t['text']} ({status_str})\n"

    user_prompt = f"""Ты CSM-менеджер компании AnyQuery. На основе данных встреч и открытых задач
напиши дружелюбный фолоуап для клиента {client.get('name', 'client')}
сегмента {client.get('segment', 'unknown')}.

Последние встречи:
{meetings_text or 'Нет данных'}

Открытые задачи:
{tasks_text or 'Нет открытых задач'}

Напиши фолоуап (2-3 абзаца) на русском, с итогами встреч и следующими шагами.
ТОЛЬКО ТЕКСТ, БЕЗ ДОПОЛНИТЕЛЬНЫХ ПОЯСНЕНИЙ."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Ты CSM-менеджер компании AnyQuery. Пишешь профессиональные фолоуапы на русском языке."
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 1000,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client_http:
            resp = await client_http.post(GROQ_URL, headers=headers, json=payload)

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content
        else:
            logger.warning("Groq API error: %s", resp.status_code)
            return ""
    except Exception as exc:
        logger.error("generate_smart_followup error: %s", exc)
        return ""


async def generate_prep_brief(client: dict, meetings: list, tasks: list) -> str:
    """
    Генерирует markdown-отформатированный brieferror для подготовки менеджера к встрече.
    Включает контекст, открытые вопросы, рекомендуемые вопросы, флаги рисков.
    """
    if not GROQ_API_KEY:
        return f"""# Подготовка к встрече: {client.get('name')}

## Основная информация
- Сегмент: {client.get('segment')}
- Последний чекап: {client.get('last_checkup') or 'не проводился'}

## Открытые задачи
(список задач будет здесь)
"""

    meetings_text = ""
    if meetings:
        for m in meetings[:3]:
            mood_emoji = "😊" if m.get("mood") == "positive" else "😐" if m.get("mood") == "neutral" else "⚠️"
            meetings_text += f"{mood_emoji} {m.get('meeting_date')}: {m.get('summary', '')[:200]}\n"

    tasks_text = ""
    if tasks:
        for t in tasks[:5]:
            status_emoji = "⏳" if t.get("status") == "open" else "🔴" if t.get("status") == "blocked" else "✅"
            tasks_text += f"{status_emoji} {t['text']}\n"

    user_prompt = f"""Ты CSM-менеджер. На основе истории встреч и открытых задач
подготовь brieferror для встречи с {client.get('name')} ({client.get('segment')}).

История встреч (последние):
{meetings_text or 'Нет встреч'}

Открытые задачи:
{tasks_text or 'Нет открытых задач'}

Сформируй brieferror в markdown с секциями:
1. **Контекст аккаунта** — краткая история, текущие приоритеты
2. **Открытые вопросы** — что нужно обсудить
3. **Рекомендуемые вопросы** — что спросить на встрече (3-4 вопроса)
4. **Флаги рисков** — если есть проблемы со здоровьем аккаунта

Ответь ТОЛЬКО markdown, без пояснений."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Ты CSM-менеджер. Готовишь brieferrors для встреч."
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 1500,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client_http:
            resp = await client_http.post(GROQ_URL, headers=headers, json=payload)

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content
        else:
            logger.warning("Groq API error in prep_brief: %s", resp.status_code)
            return ""
    except Exception as exc:
        logger.error("generate_prep_brief error: %s", exc)
        return ""


async def detect_account_risks(client: dict, meetings: list, tasks: list) -> dict:
    """
    Анализирует и возвращает риски по аккаунту.
    Возвращает:
    {
        "risk_level": "low|medium|high",
        "flags": ["флаг 1", "флаг 2", ...],
        "recommendation": "текст рекомендации"
    }
    """
    flags = []
    risk_level = "low"

    # 1. Анализируем mood тренд
    if meetings and len(meetings) >= 3:
        moods = [m.get("mood", "neutral") for m in meetings[:3]]
        if all(m in ("neutral", "risk") for m in moods):
            flags.append("Последние 3 встречи с нейтральным или негативным настроением")
            risk_level = "medium"
        if any(m == "risk" for m in moods):
            flags.append("Есть встречи с риском")
            risk_level = "medium"

    # 2. Проверяем заблокированные задачи
    blocked_count = len([t for t in tasks if t.get("status") == "blocked"])
    if blocked_count >= 3:
        flags.append(f"{blocked_count} заблокированных задач")
        risk_level = "high" if blocked_count > 5 else "medium"

    # 3. Проверяем дни с последнего чекапа
    from datetime import timedelta
    last_checkup = client.get("last_checkup")
    if last_checkup:
        try:
            last_date = date.fromisoformat(last_checkup)
            days_since = (date.today() - last_date).days
            if days_since > 60:
                flags.append(f"Чекап не проводился {days_since} дней")
                if days_since > 90:
                    risk_level = "high"
                else:
                    risk_level = "medium"
        except ValueError:
            pass
    else:
        flags.append("Чекап никогда не проводился")
        risk_level = "high"

    # 4. Проверяем задачи без дедлайна
    no_deadline = [t for t in tasks if not t.get("due_date")]
    if len(no_deadline) >= 3:
        flags.append(f"{len(no_deadline)} открытых задач без сроков")
        risk_level = "medium"

    # Генерируем рекомендацию
    recommendation = ""
    if risk_level == "high":
        recommendation = "Требуется срочный контакт с клиентом для переоценки здоровья аккаунта и решения критических блокеров."
    elif risk_level == "medium":
        recommendation = "Рекомендуется провести чекап-встречу и разобраться с открытыми вопросами."
    else:
        recommendation = "Аккаунт в норме, продолжать регулярный мониторинг."

    return {
        "risk_level": risk_level,
        "flags": flags,
        "recommendation": recommendation
    }
