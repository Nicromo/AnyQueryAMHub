"""
Планировщик — APScheduler внутри FastAPI.
Автоматические задачи:
  - 09:00 пн-пт  — утренний план в Telegram (чекапы + задачи)
  - 17:00 пт     — еженедельный дайджест в Telegram
  - каждые 60 мин — синхронизация статусов задач из Merchrules
  - каждые 60 мин — авто-импорт клиентов из Airtable CS ALL
  - каждые 30 мин — проверка напоминаний о встречах (24ч и 1ч)
  - 08:00 ежедн.  — автозадачи на чекап (просроченные клиенты)
"""
import os
import logging
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

# TG_NOTIFY_CHAT_ID — куда шлём автоматические сообщения
# Если не задан — используем ALLOWED_TG_IDS[0]
def get_notify_chat_id() -> str | None:
    chat = os.getenv("TG_NOTIFY_CHAT_ID", "")
    if chat:
        return chat
    ids = [x.strip() for x in os.getenv("ALLOWED_TG_IDS", "").split(",") if x.strip()]
    return ids[0] if ids else None


async def job_morning_plan():
    """Утренний план: просроченные чекапы + задачи на сегодня."""
    chat_id = get_notify_chat_id()
    if not chat_id:
        return

    try:
        from database import get_all_clients, get_today_overview, CHECKUP_DAYS
        from tg_bot import send_message, format_morning_plan

        # Считаем статусы
        from database import checkup_status
        clients = get_all_clients()
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )

        overview = get_today_overview()
        msg = format_morning_plan(clients, overview["urgent_tasks"], overview["week_tasks"])
        await send_message(chat_id, msg)
        logger.info("Morning plan sent to %s", chat_id)
    except Exception as exc:
        logger.error("job_morning_plan error: %s", exc)


async def job_weekly_digest():
    """Еженедельный дайджест — каждую пятницу в 17:00."""
    chat_id = get_notify_chat_id()
    if not chat_id:
        return

    try:
        from database import get_all_clients, get_all_tasks
        from tg_bot import send_message, format_weekly_digest
        from database import checkup_status

        clients = get_all_clients()
        for c in clients:
            c["status"] = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )

        open_tasks = get_all_tasks("open")
        msg = format_weekly_digest(clients, open_tasks)

        # Разбиваем на части если > 4000 символов
        for chunk in _split_message(msg, 3800):
            await send_message(chat_id, chunk)

        logger.info("Weekly digest sent to %s", chat_id)
    except Exception as exc:
        logger.error("job_weekly_digest error: %s", exc)


async def job_mr_status_sync():
    """Каждый час тянем обновлённые статусы из Merchrules."""
    try:
        from database import get_all_clients, update_task_status, get_all_tasks
        from merchrules_sync import sync_clients_from_merchrules, invalidate_cache

        invalidate_cache()
        clients = get_all_clients()
        mr_data = await sync_clients_from_merchrules(clients)

        if not mr_data:
            return

        # Обновляем статусы задач в нашей БД по результатам MR
        # (простой вариант: ищем задачи с совпадающим текстом)
        open_tasks = get_all_tasks("open")
        updated = 0

        for site_id, data in mr_data.items():
            mr_tasks = {t["title"].lower(): t["status"]
                        for t in data.get("tasks", []) if t.get("title")}

            for task in open_tasks:
                key = task["text"].lower()
                if key in mr_tasks:
                    new_status = mr_tasks[key]
                    if new_status in ("done", "completed"):
                        update_task_status(task["id"], "done")
                        updated += 1
                    elif new_status == "blocked":
                        update_task_status(task["id"], "blocked")
                        updated += 1

        if updated:
            logger.info("MR status sync: updated %d tasks", updated)

    except Exception as exc:
        logger.error("job_mr_status_sync error: %s", exc)


async def job_auto_checkup_tasks():
    """
    Ежедневно в 08:00: создаём задачу-напоминание на чекап,
    если клиент просрочен и задачи ещё нет.
    ENT — 1 раз в 30 дней, SME/SME+/SME- — 60 дней, SMB/SS — 90 дней.
    """
    try:
        from database import get_all_clients, get_client_tasks, create_internal_task, checkup_status, CHECKUP_DAYS
        clients = get_all_clients()
        created = 0

        for c in clients:
            status = checkup_status(
                c.get("last_checkup") or c.get("last_meeting"), c["segment"]
            )
            # Только просроченные (red) или предупреждения (yellow)
            if status["color"] not in ("red", "yellow"):
                continue

            # Проверяем, нет ли уже открытой задачи-напоминания
            open_tasks = get_client_tasks(c["id"], "open")
            has_reminder = any(
                "чекап" in t["text"].lower() and t.get("is_internal")
                for t in open_tasks
            )
            if has_reminder:
                continue

            # Получаем нужный интервал
            days = CHECKUP_DAYS.get(c["segment"], 90)

            # Создаём внутреннюю задачу
            create_internal_task(
                client_id=c["id"],
                text=f"🔔 Провести чекап ({c['segment']}, раз в {days} дней)",
                due_date=date.today().isoformat(),
                internal_note=f"Автозадача: последний чекап — {c.get('last_checkup') or 'не проводился'}. Статус: {status['label']}",
            )
            created += 1

        if created:
            logger.info("Auto-checkup tasks created: %d", created)

            # Уведомляем в TG если есть просрочки
            chat_id = get_notify_chat_id()
            if chat_id and created > 0:
                from tg_bot import send_message
                await send_message(chat_id, f"🔔 Создано {created} задач на чекап. Открой AM Hub → Внутренние задачи.")

    except Exception as exc:
        logger.error("job_auto_checkup_tasks error: %s", exc)


async def job_airtable_sync():
    """
    Каждый час: авто-импорт клиентов из Airtable CS ALL view.
    Upsert клиентов, автопривязка к менеджерам по display_name.
    """
    try:
        from airtable_sync import import_clients_from_airtable
        result = await import_clients_from_airtable()
        if result.get("ok"):
            logger.info(
                "Airtable auto-sync: created/updated=%d, managers_linked=%d, skipped=%d",
                result.get("created", 0),
                result.get("managers_linked", 0),
                result.get("skipped", 0),
            )
            # Если много непривязанных — логируем имена
            unmatched = result.get("unmatched_managers", {})
            if unmatched:
                logger.warning("Airtable sync: unmatched managers: %s", list(unmatched.keys()))
        else:
            logger.warning("Airtable auto-sync failed: %s", result.get("error", "?"))
    except Exception as exc:
        logger.error("job_airtable_sync error: %s", exc)


async def job_meeting_reminders():
    """
    Каждые 30 мин: проверяем planned_meeting у клиентов.
    Если до встречи ~24ч или ~1ч — отправляем напоминание менеджеру в TG и K.Talk.
    """
    try:
        from database import get_all_clients, get_all_manager_profiles, get_manager_client_ids, get_conn
        from tg_bot import send_message

        now = datetime.now()
        today = now.date()

        # Все клиенты у которых есть planned_meeting
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT c.id, c.name, c.segment, c.planned_meeting,
                       c.manager_tg_id
                FROM clients c
                WHERE c.planned_meeting IS NOT NULL
                  AND c.planned_meeting >= date('now')
                  AND c.planned_meeting <= date('now', '+2 days')
            """).fetchall()

        clients_with_meetings = [dict(r) for r in rows]
        if not clients_with_meetings:
            return

        # Профили менеджеров для уведомлений
        profiles = get_all_manager_profiles()
        tg_id_to_profile = {p["tg_id"]: p for p in profiles}

        # manager_clients для определения кому слать
        manager_client_map: dict[int, set[int]] = {}
        for p in profiles:
            ids = get_manager_client_ids(p["tg_id"])
            if ids:
                manager_client_map[p["tg_id"]] = set(ids)

        try:
            from ktalk import send_ktalk_notification
            has_ktalk = True
        except ImportError:
            has_ktalk = False

        for client in clients_with_meetings:
            planned_str = client.get("planned_meeting")
            if not planned_str:
                continue

            # planned_meeting — DATE (только дата), считаем что встреча в 10:00
            try:
                planned_date = date.fromisoformat(planned_str)
            except ValueError:
                continue

            # Считаем часы до встречи (используем начало дня встречи = 10:00)
            meet_dt = datetime.combine(planned_date, datetime.min.time().replace(hour=10))
            hours_left = (meet_dt - now).total_seconds() / 3600

            # Отправляем при 24ч (23-25) или 1ч (0.5-1.5)
            is_24h = 23 <= hours_left <= 25
            is_1h  = 0.5 <= hours_left <= 1.5

            if not (is_24h or is_1h):
                continue

            when_label = "через 24 часа" if is_24h else "через 1 час"
            msg = (
                f"📆 *Напоминание о встрече*\n"
                f"Клиент: *{client['name']}* ({client['segment']})\n"
                f"Дата: {planned_date.strftime('%d.%m.%Y')}\n"
                f"⏰ Встреча {when_label}!\n\n"
                f"Открой подготовку: /prep/{client['id']}"
            )

            # Находим менеджера этого клиента
            notified = set()
            for tg_id, client_set in manager_client_map.items():
                if client["id"] in client_set:
                    profile = tg_id_to_profile.get(tg_id, {})
                    chat = profile.get("tg_notify_chat", "")
                    if chat and tg_id not in notified:
                        await send_message(chat, msg)
                        notified.add(tg_id)

                        # K.Talk уведомление
                        if has_ktalk:
                            ktalk_webhook = profile.get("ktalk_webhook", "") or os.getenv("KTALK_WEBHOOK_URL", "")
                            if ktalk_webhook:
                                await send_ktalk_notification(
                                    webhook_url=ktalk_webhook,
                                    text=f"📆 Встреча с {client['name']} {when_label} ({planned_date.strftime('%d.%m.%Y')})",
                                )

            # Если нет привязанного менеджера — шлём на общий канал
            if not notified:
                chat_id = get_notify_chat_id()
                if chat_id:
                    await send_message(chat_id, msg)

            logger.info(
                "Meeting reminder sent: client=%s, hours_left=%.1f, notified=%s",
                client["name"], hours_left, notified or "global_chat",
            )

    except Exception as exc:
        logger.error("job_meeting_reminders error: %s", exc)


def _split_message(text: str, max_len: int = 3800) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            parts.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        parts.append(current)
    return parts


def start_scheduler():
    """Запустить планировщик при старте приложения."""
    # Утренний план — пн-пт в 9:00 МСК
    scheduler.add_job(
        job_morning_plan,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        id="morning_plan",
        replace_existing=True,
    )
    # Еженедельный дайджест — пт в 17:00
    scheduler.add_job(
        job_weekly_digest,
        CronTrigger(day_of_week="fri", hour=17, minute=0),
        id="weekly_digest",
        replace_existing=True,
    )
    # Синхронизация статусов из MR — каждые 60 минут
    scheduler.add_job(
        job_mr_status_sync,
        CronTrigger(minute=0),  # каждый час в :00
        id="mr_sync",
        replace_existing=True,
    )
    # Автозадачи на чекап — каждый день в 08:00
    scheduler.add_job(
        job_auto_checkup_tasks,
        CronTrigger(hour=8, minute=0),
        id="auto_checkup_tasks",
        replace_existing=True,
    )
    # Авто-импорт из Airtable — каждый час в :30
    scheduler.add_job(
        job_airtable_sync,
        CronTrigger(minute=30),
        id="airtable_sync",
        replace_existing=True,
    )
    # Напоминания о встречах — каждые 30 минут
    scheduler.add_job(
        job_meeting_reminders,
        CronTrigger(minute="0,30"),
        id="meeting_reminders",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: morning_plan (9:00 пн-пт), weekly_digest (пт 17:00), "
        "mr_sync (каждый час в :00), airtable_sync (каждый час в :30), "
        "meeting_reminders (каждые 30 мин), auto_checkup_tasks (08:00)"
    )
