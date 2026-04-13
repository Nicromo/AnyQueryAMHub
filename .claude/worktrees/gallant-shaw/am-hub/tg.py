"""
Telegram Bot API — отправка фолоуапа в канал клиента.
"""
import httpx
from datetime import date


MOOD_EMOJI = {"positive": "🟢", "neutral": "🟡", "risk": "🔴"}
TYPE_LABEL = {"checkup": "Чекап", "qbr": "QBR", "urgent": "Экстренная встреча"}


def build_followup_message(
    client_name: str,
    meeting_date: str,
    meeting_type: str,
    summary: str,
    aq_tasks: list[dict],
    client_tasks: list[dict],
    next_meeting: str | None,
    mood: str,
) -> str:
    """Формирует текст сообщения для TG-канала клиента."""
    mood_icon = MOOD_EMOJI.get(mood, "🟡")
    type_label = TYPE_LABEL.get(meeting_type, "Встреча")

    lines = [
        f"{mood_icon} *{type_label} — {client_name}*",
        f"📅 {meeting_date}",
        "",
        "📝 *Что обсудили:*",
        summary or "_не заполнено_",
    ]

    if aq_tasks:
        lines += ["", "✅ *Задачи AnyQuery:*"]
        for t in aq_tasks:
            due = f" _(до {t['due_date']})_" if t.get("due_date") else ""
            lines.append(f"• {t['text']}{due}")

    if client_tasks:
        lines += ["", "🔷 *Задачи партнёра:*"]
        for t in client_tasks:
            due = f" _(до {t['due_date']})_" if t.get("due_date") else ""
            lines.append(f"• {t['text']}{due}")

    if next_meeting:
        lines += ["", f"📆 *Следующая встреча:* {next_meeting}"]

    lines += ["", "—", "_anyquery AM Hub_"]
    return "\n".join(lines)


async def send_to_tg(bot_token: str, chat_id: str, text: str) -> bool:
    """Отправляет сообщение в TG-канал. Возвращает True при успехе."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })
    return resp.status_code == 200
