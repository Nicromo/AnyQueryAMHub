"""
Telegram Bot — обработчик входящих команд через Webhook.
Поддерживаемые команды:
  /start   — привет + список команд
  /help    — список команд
  /top50   — Top-50 клиентов из Google Sheets
  /checkups — список просроченных чекапов
"""
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
ALLOWED_IDS: set[int] = {
    int(x) for x in os.getenv("ALLOWED_TG_IDS", "").split(",") if x.strip()
}


async def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
    disable_notification: bool = False,
) -> bool:
    """Отправить сообщение через Telegram Bot API."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            })
        return resp.status_code == 200
    except Exception as exc:
        logger.error("TG send_message error: %s", exc)
        return False


def is_allowed(user_id: int) -> bool:
    """Проверка доступа по TG ID."""
    if not ALLOWED_IDS:
        return True
    return user_id in ALLOWED_IDS


def format_top50_for_tg(data: dict, mode: str = "weekly") -> str:
    """
    Форматирует данные Top-50 в текст для Telegram.
    mode: "weekly" — проблемы/пробелы, "monthly" — аналитика клиентов.
    """
    if data.get("error"):
        return f"❌ {data['error']}"

    rows = data.get("filtered_rows") or []
    if not rows:
        rows = data.get("rows") or []

    if not rows:
        return "📭 Данных в таблице нет или нет строк по вашим клиентам."

    headers = data.get("headers", [])
    client_col = data.get("client_col")
    problem_cols = data.get("problem_cols", [])
    fetched_at = data.get("fetched_at", "")

    title = "📊 <b>Top-50 — еженедельный отчёт</b>" if mode == "weekly" \
        else "📅 <b>Top-50 — ежемесячный анализ</b>"

    lines = [title, f"<i>Данные: {fetched_at}</i>", ""]

    for i, row in enumerate(rows[:50], 1):
        client_name = row.get(client_col, "—") if client_col else "—"
        line = f"<b>{i}. {client_name}</b>"

        if problem_cols:
            problems = [row.get(c, "").strip() for c in problem_cols if row.get(c, "").strip()]
            if problems:
                line += "\n   " + " | ".join(problems)
        else:
            # Показываем все непустые значения кроме имени клиента
            extras = []
            for h in headers:
                if h == client_col:
                    continue
                v = row.get(h, "").strip()
                if v:
                    extras.append(f"{h}: {v}")
            if extras:
                line += "\n   " + " | ".join(extras[:3])

        lines.append(line)

    lines.append(f"\n<i>Показано: {min(len(rows), 50)} из {len(rows)} строк</i>")
    return "\n".join(lines)


def format_overdue_checkups(clients: list[dict]) -> str:
    """Форматирует список просроченных чекапов для TG."""
    overdue = [
        c for c in clients
        if c.get("status", {}).get("color") == "red"
    ]
    if not overdue:
        return "✅ <b>Просроченных чекапов нет!</b> Всё под контролем."

    lines = [f"🔴 <b>Просроченные чекапы ({len(overdue)})</b>", ""]
    for c in overdue:
        label = c.get("status", {}).get("label", "Просрочен")
        lines.append(f"• <b>{c['name']}</b> [{c['segment']}] — {label}")

    warning = [
        c for c in clients
        if c.get("status", {}).get("color") == "yellow"
    ]
    if warning:
        lines.append(f"\n🟡 <b>Скоро ({len(warning)})</b>")
        for c in warning[:5]:
            label = c.get("status", {}).get("label", "")
            lines.append(f"• <b>{c['name']}</b> — {label}")
        if len(warning) > 5:
            lines.append(f"  <i>…и ещё {len(warning) - 5}</i>")

    return "\n".join(lines)


async def handle_update(update: dict, get_clients_fn, get_top50_fn) -> None:
    """
    Обрабатывает входящий Update от Telegram.
    get_clients_fn — синхронная функция, возвращающая список клиентов из БД.
    get_top50_fn   — async-функция, возвращающая данные Top-50.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id", 0)
    text = (message.get("text") or "").strip()

    if not chat_id or not text.startswith("/"):
        return

    if not is_allowed(user_id):
        await send_message(chat_id, "⛔ Доступ закрыт.")
        return

    cmd = text.split()[0].lower().split("@")[0]  # /cmd@botname → /cmd

    if cmd in ("/start", "/help"):
        await send_message(chat_id, (
            "👋 <b>AM Hub Bot</b>\n\n"
            "Доступные команды:\n"
            "/checkups — просроченные чекапы\n"
            "/top50 — Top-50 клиентов (еженедельный)\n"
            "/top50m — Top-50 клиентов (ежемесячный)\n"
            "/help — эта справка"
        ))

    elif cmd == "/checkups":
        clients = get_clients_fn()
        # Добавляем статус к каждому клиенту
        from main import checkup_status
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )
        msg = format_overdue_checkups(clients)
        await send_message(chat_id, msg)

    elif cmd in ("/top50", "/top50m"):
        mode = "monthly" if cmd == "/top50m" else "weekly"
        await send_message(chat_id, "⏳ Загружаю данные из таблицы…", disable_notification=True)
        data = await get_top50_fn()
        msg = format_top50_for_tg(data, mode=mode)
        # Разбиваем на части если слишком длинное (лимит TG 4096 символов)
        chunk_size = 3800
        if len(msg) <= chunk_size:
            await send_message(chat_id, msg)
        else:
            parts = []
            current = ""
            for line in msg.split("\n"):
                if len(current) + len(line) + 1 > chunk_size:
                    parts.append(current)
                    current = line
                else:
                    current += "\n" + line if current else line
            if current:
                parts.append(current)
            for part in parts:
                await send_message(chat_id, part)

    else:
        await send_message(chat_id, f"❓ Неизвестная команда: {cmd}\nНапиши /help")


async def set_webhook(webhook_url: str) -> bool:
    """Регистрирует webhook у Telegram."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"url": webhook_url})
        data = resp.json()
        ok = data.get("ok", False)
        if not ok:
            logger.error("setWebhook failed: %s", data)
        return ok
    except Exception as exc:
        logger.error("setWebhook exception: %s", exc)
        return False


async def delete_webhook() -> bool:
    """Удаляет webhook (например при локальном запуске)."""
    if not BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url)
        return resp.json().get("ok", False)
    except Exception:
        return False
