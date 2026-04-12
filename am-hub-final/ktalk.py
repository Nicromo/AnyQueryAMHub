"""
K.Talk интеграция — корпоративный мессенджер T-Bank.
https://tbank.ktalk.ru/

Поддерживает:
  - Исходящие уведомления через Incoming Webhook (как Slack)
  - Настройка webhook URL в профиле менеджера или через env KTALK_WEBHOOK_URL

Настройка K.Talk Webhook:
  1. В K.Talk: Настройки канала → Интеграции → Incoming Webhook → Создать
  2. Скопировать URL вида https://tbank.ktalk.ru/hooks/XXXXX
  3. Вставить в профиль AM Hub (поле K.Talk Webhook) или в Railway Variables (KTALK_WEBHOOK_URL)
"""
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

KTALK_WEBHOOK_URL = os.getenv("KTALK_WEBHOOK_URL", "")


async def send_ktalk_notification(
    text: str,
    webhook_url: Optional[str] = None,
    username: str = "AM Hub",
    icon_emoji: str = "🏢",
) -> dict:
    """
    Отправить уведомление в K.Talk через Incoming Webhook.

    K.Talk поддерживает формат совместимый с Mattermost/Slack.
    Возвращает {"ok": True} или {"ok": False, "error": "..."}
    """
    url = webhook_url or KTALK_WEBHOOK_URL
    if not url:
        logger.debug("K.Talk webhook URL not configured, skipping")
        return {"ok": False, "error": "KTALK_WEBHOOK_URL не задан"}

    payload = {
        "text": text,
        "username": username,
        "icon_emoji": icon_emoji,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201):
                logger.debug("K.Talk notification sent: %s...", text[:50])
                return {"ok": True}
            else:
                logger.warning("K.Talk error: HTTP %d — %s", resp.status_code, resp.text[:200])
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        logger.warning("K.Talk send error: %s", exc)
        return {"ok": False, "error": str(exc)}


async def send_ktalk_followup(
    client_name: str,
    meeting_date: str,
    meeting_type: str,
    summary: str,
    mood: str,
    aq_tasks: list[dict],
    next_meeting: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> dict:
    """
    Отправить фолоуап встречи в K.Talk канал.
    Форматирует как структурированное сообщение.
    """
    mood_emoji = {"positive": "🟢", "neutral": "🟡", "risk": "🔴"}.get(mood, "🟡")
    type_label = {"checkup": "Чекап", "qbr": "QBR", "urgent": "Экстренная"}.get(meeting_type, "Встреча")

    lines = [
        f"{mood_emoji} **{type_label} — {client_name}** · {meeting_date}",
    ]
    if summary:
        lines += ["", summary]
    if aq_tasks:
        lines += ["", "**Задачи AnyQuery:**"]
        for t in aq_tasks:
            due = f" (до {t['due_date']})" if t.get("due_date") else ""
            lines.append(f"• {t['text']}{due}")
    if next_meeting:
        lines += ["", f"📆 Следующая встреча: {next_meeting}"]
    lines += ["", "_anyquery AM Hub_"]

    return await send_ktalk_notification(
        text="\n".join(lines),
        webhook_url=webhook_url,
    )


async def send_ktalk_digest(
    stats: dict,
    webhook_url: Optional[str] = None,
) -> dict:
    """
    Отправить дайджест в K.Talk.
    stats: {overdue, warning, open_tasks, managers_count}
    """
    text = (
        f"📊 **Еженедельный дайджест AM Hub**\n\n"
        f"🔴 Просроченных чекапов: **{stats.get('overdue', 0)}**\n"
        f"🟡 Скоро чекап: **{stats.get('warning', 0)}**\n"
        f"📋 Открытых задач: **{stats.get('open_tasks', 0)}**\n"
        f"👥 Активных менеджеров: **{stats.get('managers_count', 0)}**"
    )
    return await send_ktalk_notification(text=text, webhook_url=webhook_url)


async def test_ktalk_connection(webhook_url: str) -> dict:
    """Проверить подключение к K.Talk."""
    return await send_ktalk_notification(
        text="✅ AM Hub подключён к K.Talk! Уведомления о встречах и чекапах будут приходить сюда.",
        webhook_url=webhook_url,
    )
