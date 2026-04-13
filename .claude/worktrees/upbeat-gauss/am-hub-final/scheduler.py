"""
Планировщик — APScheduler внутри FastAPI.
Автоматические задачи:
  - 09:00 пн-пт  — утренний план в Telegram (чекапы + задачи)
  - 17:00 пт     — еженедельный дайджест в Telegram
  - каждые 60 мин — синхронизация статусов задач из Merchrules
"""
import os
import logging
from datetime import date, timedelta

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
    scheduler.start()
    logger.info("Scheduler started: morning_plan (9:00 пн-пт), weekly_digest (пт 17:00), mr_sync (каждый час)")
