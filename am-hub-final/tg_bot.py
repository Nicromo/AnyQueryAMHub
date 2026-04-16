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
from datetime import date, timedelta
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
    args = text.split()[1:] if len(text.split()) > 1 else []
    arg_str = " ".join(args).strip() if args else ""

    if cmd in ("/start", "/help"):
        await send_message(chat_id, (
            "👋 <b>AM Hub Bot</b>\n\n"
            "Доступные команды:\n"
            "/today — мой день: встречи + слоты подготовки\n"
            "/status — общая статистика\n"
            "/checkups — просроченные чекапы\n"
            "/checkup &lt;name&gt; — статус клиента\n"
            "/tasks &lt;name&gt; — список задач клиента\n"
            "/done &lt;task_id&gt; — закрыть задачу\n"
            "/prep &lt;name&gt; — подготовка к встрече\n"
            "/clients — мои клиенты\n"
            "/top50 — Top-50 (еженедельный)\n"
            "/top50m — Top-50 (ежемесячный)\n"
            "/help — эта справка"
        ))

    elif cmd == "/today":
        # Слоты дня из БД
        try:
            from database import SessionLocal
            from models import User as UserModel
            from meeting_slots import get_day_slots
            from datetime import timezone as tz_mod
            MSK = tz_mod(timedelta(hours=3))
            db = SessionLocal()

            # Ищем пользователя по telegram_id
            tg_user = db.query(UserModel).filter(
                UserModel.telegram_id == str(user_id)
            ).first()

            if not tg_user:
                db.close()
                await send_message(chat_id, "❌ Ваш Telegram не привязан к аккаунту AM Hub.")
                return

            today = datetime.now(MSK).replace(tzinfo=None)
            slots = get_day_slots(db, tg_user.email, today)
            db.close()

            if not slots:
                await send_message(chat_id, f"📅 <b>{today.strftime('%d.%m.%Y')}</b>\n\nНа сегодня встреч и слотов нет.")
                return

            lines = [f"📅 <b>Мой день — {today.strftime('%d.%m.%Y')}</b>\n"]
            for s in slots:
                time_str = s.get("time", "—")
                title = s.get("title", "")
                s_type = s.get("type", "")
                if s_type == "meeting":
                    mtype = s.get("meeting_type", "")
                    icon = {"qbr": "🔵", "checkup": "🟢", "kickoff": "🟠",
                            "onboarding": "🟠", "upsell": "💚", "downsell": "🔴"}.get(mtype, "⚫")
                    lines.append(f"{icon} <b>{time_str}</b> — {title}")
                elif s_type == "slot":
                    slot_type = s.get("slot_type", "prep")
                    icon = {"prep": "📋", "followup": "✍️", "extra": "📌"}.get(slot_type, "📌")
                    status = s.get("status", "plan")
                    done_mark = " ✅" if status == "done" else ""
                    lines.append(f"  {icon} до <b>{time_str}</b> — {title}{done_mark}")

            await send_message(chat_id, "\n".join(lines))

        except Exception as e:
            logger.error(f"TG /today error: {e}")
            await send_message(chat_id, "❌ Ошибка при загрузке слотов дня.")


        clients = get_clients_fn()
        from database import checkup_status
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )
        overdue = [c for c in clients if c.get("status", {}).get("color") == "red"]
        warning = [c for c in clients if c.get("status", {}).get("color") == "yellow"]
        all_tasks = get_all_tasks("open") if "get_all_tasks" in dir() else []
        msg = (
            f"📊 <b>Статус AM Hub</b>\n\n"
            f"👥 Всего клиентов: {len(clients)}\n"
            f"🔴 Просроченных: {len(overdue)}\n"
            f"🟡 Требуют внимания: {len(warning)}\n"
            f"📋 Открытых задач: {len([t for t in all_tasks if t.get('status') == 'open'])}"
        )
        await send_message(chat_id, msg)

    elif cmd == "/checkups":
        clients = get_clients_fn()
        # Добавляем статус к каждому клиенту
        from database import checkup_status
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )
        msg = format_overdue_checkups(clients)
        await send_message(chat_id, msg)

    elif cmd == "/checkup" and arg_str:
        from database import get_all_clients, get_client_tasks, checkup_status
        clients = get_all_clients()
        # Fuzzy search по имени
        matches = [c for c in clients if arg_str.lower() in c["name"].lower()]
        if not matches:
            await send_message(chat_id, f"❌ Клиент '{arg_str}' не найден")
            return
        if len(matches) > 1:
            names = ", ".join([m["name"] for m in matches[:5]])
            await send_message(chat_id, f"🔍 Найдено {len(matches)} совпадений: {names}")
            return
        client = matches[0]
        status = checkup_status(
            client.get("last_checkup") or client.get("last_meeting"), client["segment"]
        )
        tasks = get_client_tasks(client["id"], "open")
        msg = (
            f"📌 <b>{client['name']}</b> [{client['segment']}]\n\n"
            f"Статус чекапа: {status['label']}\n"
            f"Последний чекап: {client.get('last_checkup') or client.get('last_meeting') or 'Не проводился'}\n"
            f"Открытых задач: {len(tasks)}"
        )
        await send_message(chat_id, msg)

    elif cmd == "/tasks" and arg_str:
        from database import get_all_clients, get_client_tasks
        clients = get_all_clients()
        matches = [c for c in clients if arg_str.lower() in c["name"].lower()]
        if not matches:
            await send_message(chat_id, f"❌ Клиент '{arg_str}' не найден")
            return
        if len(matches) > 1:
            names = ", ".join([m["name"] for m in matches[:5]])
            await send_message(chat_id, f"🔍 Найдено {len(matches)} совпадений: {names}")
            return
        client = matches[0]
        tasks = get_client_tasks(client["id"], "open")
        if not tasks:
            await send_message(chat_id, f"✅ У {client['name']} нет открытых задач")
            return
        lines = [f"📋 <b>Задачи {client['name']}</b>", ""]
        for t in tasks[:10]:
            status_icon = "🔴" if t.get("status") == "blocked" else "⏳"
            due = f" (до {t['due_date']})" if t.get("due_date") else ""
            lines.append(f"{status_icon} #{t['id']}: {t['text'][:60]}{due}")
        if len(tasks) > 10:
            lines.append(f"\n…и ещё {len(tasks)-10} задач")
        await send_message(chat_id, "\n".join(lines))

    elif cmd == "/done" and arg_str:
        try:
            from database import update_task_status, get_conn
            task_id = int(arg_str)
            update_task_status(task_id, "done")
            await send_message(chat_id, f"✅ Задача #{task_id} закрыта")
        except (ValueError, Exception) as e:
            await send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")

    elif cmd == "/prep" and arg_str:
        from database import get_all_clients, get_client_meetings, get_client_tasks
        clients = get_all_clients()
        matches = [c for c in clients if arg_str.lower() in c["name"].lower()]
        if not matches:
            await send_message(chat_id, f"❌ Клиент '{arg_str}' не найден")
            return
        if len(matches) > 1:
            names = ", ".join([m["name"] for m in matches[:5]])
            await send_message(chat_id, f"🔍 Найдено {len(matches)} совпадений: {names}")
            return
        client = matches[0]
        meetings = get_client_meetings(client["id"], limit=3)
        tasks = get_client_tasks(client["id"], "open")

        lines = [f"📚 <b>Подготовка к встрече: {client['name']}</b>", ""]

        if meetings:
            lines.append("<b>Последние встречи:</b>")
            for m in meetings[:2]:
                lines.append(f"• {m.get('meeting_date')}: {m.get('summary', 'без описания')[:80]}")
            lines.append("")

        if tasks:
            lines.append(f"<b>Открытые задачи ({len(tasks)}):</b>")
            for t in tasks[:5]:
                lines.append(f"• {t['text'][:70]}")
            if len(tasks) > 5:
                lines.append(f"  и ещё {len(tasks)-5}…")
            lines.append("")

        lines.append("<b>Рекомендуемые вопросы:</b>")
        lines.append("• Как идут работы со следующей задачей?")
        lines.append("• Есть ли новые потребности или проблемы?")
        lines.append("• Когда планируем встречу на следующий месяц?")

        await send_message(chat_id, "\n".join(lines))

    elif cmd == "/clients":
        from database import get_all_clients
        clients = get_all_clients()
        by_segment = {}
        for c in clients:
            seg = c["segment"]
            if seg not in by_segment:
                by_segment[seg] = []
            by_segment[seg].append(c)

        lines = ["👥 <b>Мои клиенты</b>", ""]
        for seg in ["ENT", "SME+", "SME", "SME-", "SMB", "SS"]:
            if seg in by_segment:
                lines.append(f"<b>{seg} ({len(by_segment[seg])})</b>")
                for c in by_segment[seg][:5]:
                    lines.append(f"  • {c['name']}")
                if len(by_segment[seg]) > 5:
                    lines.append(f"    и ещё {len(by_segment[seg])-5}…")
        await send_message(chat_id, "\n".join(lines))

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


def get_all_tasks(status: str = "open") -> list:
    """Получает все задачи из БД."""
    try:
        from database import get_all_tasks as db_get_all_tasks
        return db_get_all_tasks(status)
    except Exception:
        return []


def format_morning_plan(clients: list, urgent_tasks: list, week_tasks: list) -> str:
    """Утренний план — что делать сегодня."""
    today = date.today()
    weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    day = weekdays[today.weekday()]

    lines = [f"☀️ <b>Доброе утро! {day}, {today.strftime('%d.%m')}</b>", ""]

    overdue = [c for c in clients if c.get("status", {}).get("color") == "red"]
    warning = [c for c in clients if c.get("status", {}).get("color") == "yellow"]

    if overdue:
        lines.append(f"🔴 <b>Просрочены чекапы ({len(overdue)}):</b>")
        for c in overdue[:5]:
            lines.append(f"  • {c['name']} [{c['segment']}] — {c['status']['label']}")
        if len(overdue) > 5:
            lines.append(f"  <i>…и ещё {len(overdue)-5}</i>")
        lines.append("")

    if warning:
        lines.append(f"🟡 <b>Скоро нужен чекап ({len(warning)}):</b>")
        for c in warning[:3]:
            lines.append(f"  • {c['name']} — {c['status']['label']}")
        lines.append("")

    if urgent_tasks:
        lines.append(f"🔥 <b>Горящие задачи ({len(urgent_tasks)}):</b>")
        for t in urgent_tasks[:5]:
            due = t.get("due_date", "")
            lines.append(f"  • {t['client_name']} — {t['text'][:50]}"
                         + (f" (дедлайн {due})" if due else ""))
        lines.append("")

    if week_tasks:
        lines.append(f"📋 <b>Задачи на неделю ({len(week_tasks)}):</b>")
        for t in week_tasks[:5]:
            lines.append(f"  • {t['client_name']} — {t['text'][:50]}")
        lines.append("")

    if not overdue and not urgent_tasks:
        lines.append("✅ <b>Всё под контролем!</b> Просроченных задач нет.")

    lines.append("<i>AM Hub · /checkups для деталей</i>")
    return "\n".join(lines)


def format_weekly_digest(clients: list, open_tasks: list) -> str:
    """Еженедельный дайджест — итоги недели по всем клиентам."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).strftime("%d.%m")

    lines = [
        f"📊 <b>Еженедельный дайджест AM Hub</b>",
        f"<i>Неделя с {week_start}</i>",
        "",
    ]

    overdue = [c for c in clients if c.get("status", {}).get("color") == "red"]
    ok = [c for c in clients if c.get("status", {}).get("color") == "green"]
    total = len(clients)

    lines.append(f"👥 <b>Клиенты: {total}</b>")
    lines.append(f"  🔴 Просрочено чекапов: {len(overdue)}")
    lines.append(f"  ✅ В норме: {len(ok)}")
    lines.append(f"  📋 Открытых задач: {len(open_tasks)}")
    lines.append("")

    blocked = [t for t in open_tasks if t.get("status") == "blocked"]
    if blocked:
        lines.append(f"🚫 <b>Заблокированных задач: {len(blocked)}</b>")
        for t in blocked[:5]:
            lines.append(f"  • {t['client_name']} — {t['text'][:50]}")
        lines.append("")

    if overdue:
        lines.append(f"⚠️ <b>Требуют чекапа:</b>")
        for c in overdue[:8]:
            lines.append(f"  • {c['name']} [{c['segment']}] — {c['status']['label']}")
        lines.append("")

    # Клиенты без единой задачи
    clients_with_tasks = {t["client_id"] for t in open_tasks}
    ent_no_tasks = [c for c in clients if c["segment"] == "ENT"
                    and c["id"] not in clients_with_tasks]
    if ent_no_tasks:
        lines.append(f"📭 <b>ENT без открытых задач ({len(ent_no_tasks)}):</b>")
        for c in ent_no_tasks:
            lines.append(f"  • {c['name']}")
        lines.append("")

    lines.append("<i>AM Hub · хорошей недели!</i>")
    return "\n".join(lines)


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
