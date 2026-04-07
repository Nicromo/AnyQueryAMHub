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


async def job_pre_meeting_brief():
    """
    Каждые 30 минут проверяет: есть ли встреча через ~1 час (±15 мин).
    Если да — отправляет AI-бриф AM в Telegram.
    """
    try:
        from database import get_all_clients, get_client_meetings, get_client_tasks, calculate_health_score
        from ai_followup import generate_pre_meeting_brief
        from tg_bot import send_message
        from datetime import datetime as dt, date, timedelta

        now = dt.now()
        today = date.today().isoformat()
        target_time_min = (now + timedelta(minutes=45)).strftime("%H:%M")
        target_time_max = (now + timedelta(minutes=75)).strftime("%H:%M")

        clients = get_all_clients()

        for c in clients:
            # Встреча запланирована на сегодня
            if c.get("planned_meeting") != today:
                continue

            # Проверяем время встречи (если указано)
            meeting_time = c.get("meeting_time", "")
            if meeting_time:
                if not (target_time_min <= meeting_time <= target_time_max):
                    continue
            else:
                # Если время не указано — шлём в 09:00
                if now.hour != 9 or now.minute > 15:
                    continue

            # Определяем чат AM
            chat_id = get_notify_chat_id()
            if not chat_id:
                continue

            meetings = get_client_meetings(c["id"], limit=3)
            open_tasks = get_client_tasks(c["id"], "open")
            health = calculate_health_score(c["id"])

            brief = await generate_pre_meeting_brief(c, meetings, open_tasks, health)

            header = f"📋 <b>Бриф перед встречей: {c['name']}</b> [{c['segment']}]\n\n"
            await send_message(chat_id, header + brief)
            logger.info("Pre-meeting brief sent for client %s", c["name"])

    except Exception as exc:
        logger.error("job_pre_meeting_brief error: %s", exc)


async def job_followup_reminder():
    """
    Каждый час: проверяет встречи прошедшие ~1 час назад без фолоуапа.
    Напоминает AM отправить фолоуап.
    """
    try:
        from database import get_followup_pending
        from tg_bot import send_message

        pending = get_followup_pending(days_back=1)
        chat_id = get_notify_chat_id()
        if not chat_id or not pending:
            return

        for meeting in pending:
            # Только встречи за сегодня
            if meeting["meeting_date"] != __import__("datetime").date.today().isoformat():
                continue

            client_name = meeting.get("client_name", "клиент")
            msg = (
                f"⏰ <b>Напоминание: фолоуап</b>\n\n"
                f"Встреча с <b>{client_name}</b> прошла сегодня.\n"
                f"Постмит / фолоуап ещё не отправлен клиенту.\n\n"
                f"👉 Открой AM Hub → карточка клиента → кнопка «Отправить фолоуап»"
            )
            await send_message(chat_id, msg)
            logger.info("Followup reminder sent for meeting %d", meeting["id"])

    except Exception as exc:
        logger.error("job_followup_reminder error: %s", exc)


async def job_health_score_update():
    """
    Каждый день в 07:00: пересчитывает health score всех клиентов
    и сохраняет снапшот для трекинга тренда.
    """
    try:
        from database import get_all_clients, update_client_health_score, save_health_snapshot

        clients = get_all_clients()
        updated = 0
        for c in clients:
            result = update_client_health_score(c["id"])
            save_health_snapshot(c["id"], result["score"], result["color"])
            updated += 1

        logger.info("Health score updated for %d clients", updated)

    except Exception as exc:
        logger.error("job_health_score_update error: %s", exc)


async def job_metrics_degradation_check():
    """
    Каждый день в 10:00: проверяет health score на деградацию.
    Если score упал > 20 баллов за последние 7 дней — алёрт AM.
    """
    try:
        from database import get_all_clients, get_health_history, create_internal_task
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        clients = get_all_clients()
        alerts = []

        for c in clients:
            history = get_health_history(c["id"], months=1)
            if len(history) < 2:
                continue
            latest = history[-1]
            week_ago = next(
                (h for h in reversed(history[:-1])
                 if h["snapshot_date"] <= (__import__("datetime").date.today() - __import__("datetime").timedelta(days=7)).isoformat()),
                None
            )
            if not week_ago:
                continue
            drop = week_ago["health_score"] - latest["health_score"]
            if drop >= 20:
                alerts.append({
                    "client": c["name"],
                    "segment": c["segment"],
                    "was": week_ago["health_score"],
                    "now": latest["health_score"],
                    "drop": drop,
                })
                # Создаём задачу
                create_internal_task(
                    client_id=c["id"],
                    text=f"⚠️ Деградация Health Score: {week_ago['health_score']} → {latest['health_score']} (-{drop} за неделю)",
                    internal_note="Автозадача: проверить метрики клиента, провести внеплановый созвон"
                )

        if alerts and chat_id:
            lines = ["🚨 <b>Деградация Health Score</b>\n"]
            for a in alerts:
                lines.append(f"• <b>{a['client']}</b> [{a['segment']}]: {a['was']} → {a['now']} (-{a['drop']} за неделю)")
            lines.append("\n👉 Открой AM Hub → клиент → проверь задачи")
            await send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        logger.error("job_metrics_degradation_check error: %s", exc)


async def job_qbr_cycle_check():
    """
    Каждый понедельник: проверяет ENT-клиентов у которых QBR не проводился > 80 дней.
    """
    try:
        from database import get_all_clients, get_client_meetings
        from tg_bot import send_message
        from datetime import date, timedelta

        chat_id = get_notify_chat_id()
        if not chat_id:
            return

        clients = get_all_clients()
        today = date.today()
        warnings = []

        for c in clients:
            if c["segment"] not in ("ENT", "SME+"):
                continue
            meetings = get_client_meetings(c["id"], limit=20)
            qbrs = [m for m in meetings if m.get("meeting_type") == "qbr"]
            if not qbrs:
                days_since = 999
                last_qbr = "никогда"
            else:
                last_qbr_date = date.fromisoformat(qbrs[0]["meeting_date"])
                days_since = (today - last_qbr_date).days
                last_qbr = qbrs[0]["meeting_date"]

            threshold = 80 if c["segment"] == "ENT" else 90
            if days_since >= threshold:
                warnings.append({
                    "name": c["name"],
                    "segment": c["segment"],
                    "days": days_since,
                    "last_qbr": last_qbr,
                })

        if warnings:
            lines = [f"📆 <b>QBR нужен срочно ({len(warnings)} клиентов)</b>\n"]
            for w in warnings:
                lines.append(f"• <b>{w['name']}</b> [{w['segment']}] — последний QBR: {w['last_qbr']} ({w['days']} дней назад)")
            lines.append("\n👉 AM Hub → QBR Календарь")
            await send_message(chat_id, "\n".join(lines))
            logger.info("QBR cycle check: %d warnings sent", len(warnings))

    except Exception as exc:
        logger.error("job_qbr_cycle_check error: %s", exc)


async def job_personal_digest():
    """
    Каждую пятницу в 17:30: персональный дайджест каждому AM в личку.
    """
    try:
        from database import (
            get_all_manager_tg_ids, get_manager_client_ids,
            get_client, get_client_tasks, checkup_status, get_client_meetings
        )
        from tg_bot import send_message
        from datetime import date

        manager_ids = get_all_manager_tg_ids()
        today = date.today()

        for tg_id in manager_ids:
            client_ids = get_manager_client_ids(tg_id)
            if not client_ids:
                continue

            clients_data = [get_client(cid) for cid in client_ids if get_client(cid)]
            total = len(clients_data)
            red_clients = []
            overdue_tasks = []
            all_open = 0

            for c in clients_data:
                status = checkup_status(
                    c.get("last_checkup") or c.get("last_meeting"), c["segment"]
                )
                if status["color"] == "red":
                    red_clients.append(c["name"])

                tasks = get_client_tasks(c["id"], "open")
                all_open += len(tasks)
                for t in tasks:
                    if t.get("due_date") and t["due_date"] < today.isoformat():
                        overdue_tasks.append(f"{c['name']}: {t['text'][:50]}")

            lines = [
                f"📊 <b>Твой дайджест за неделю</b>",
                f"<i>Пятница, {today.strftime('%d.%m.%Y')}</i>",
                "",
                f"👥 Клиентов в портфеле: {total}",
                f"📋 Открытых задач: {all_open}",
                f"🔥 Просроченных задач: {len(overdue_tasks)}",
                f"🔴 Клиентов в red: {len(red_clients)}",
            ]

            if red_clients:
                lines.append(f"\n🔴 <b>Требуют внимания:</b>")
                for name in red_clients[:5]:
                    lines.append(f"  • {name}")

            if overdue_tasks:
                lines.append(f"\n⚠️ <b>Просроченные задачи:</b>")
                for t in overdue_tasks[:5]:
                    lines.append(f"  • {t}")

            lines.append("\n<i>AM Hub · хорошей недели! 🚀</i>")

            await send_message(tg_id, "\n".join(lines))
            logger.info("Personal digest sent to manager %d", tg_id)

    except Exception as exc:
        logger.error("job_personal_digest error: %s", exc)


async def job_chat_activity_reminder():
    """
    Каждый вторник: напоминание AM о клиентах без коммуникации.
    """
    try:
        from database import get_clients_without_recent_chat
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        if not chat_id:
            return

        clients = get_clients_without_recent_chat()
        overdue = [c for c in clients if c.get("chat_overdue")]

        if not overdue:
            return

        lines = [f"💬 <b>Давно не писали клиентам ({len(overdue)})</b>\n"]
        for c in overdue[:10]:
            days = c.get("days_since_chat", 0)
            norm = c.get("chat_norm_days", 30)
            name = c.get("name", "?")
            segment = c.get("segment", "?")
            label = "никогда" if days > 900 else f"{days} дней"
            lines.append(f"• <b>{name}</b> [{segment}] — {label} (норма {norm} дн.)")

        lines.append("\n👉 AM Hub → Активность в чатах")
        await send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        logger.error("job_chat_activity_reminder error: %s", exc)


async def job_recurring_tasks():
    """
    Каждый день в 08:30: создаёт новые копии повторяющихся задач.
    """
    try:
        from database import get_recurring_tasks_to_create, create_recurring_copy

        tasks = get_recurring_tasks_to_create()
        for task in tasks:
            create_recurring_copy(task)
            logger.info("Recurring task created: %s for client %d", task["text"][:50], task["client_id"])

        if tasks:
            logger.info("Recurring tasks job: created %d tasks", len(tasks))

    except Exception as exc:
        logger.error("job_recurring_tasks error: %s", exc)


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
    # Обновление Health Score — каждый день в 07:00
    scheduler.add_job(
        job_health_score_update,
        CronTrigger(hour=7, minute=0),
        id="health_score_update",
        replace_existing=True,
    )
    # Проверка деградации метрик — каждый день в 10:00
    scheduler.add_job(
        job_metrics_degradation_check,
        CronTrigger(hour=10, minute=0),
        id="metrics_degradation",
        replace_existing=True,
    )
    # QBR-цикл — каждый понедельник в 09:30
    scheduler.add_job(
        job_qbr_cycle_check,
        CronTrigger(day_of_week="mon", hour=9, minute=30),
        id="qbr_cycle_check",
        replace_existing=True,
    )
    # Персональный дайджест AM — пятница 17:30
    scheduler.add_job(
        job_personal_digest,
        CronTrigger(day_of_week="fri", hour=17, minute=30),
        id="personal_digest",
        replace_existing=True,
    )
    # Напоминание о коммуникации в чатах — вторник 10:00
    scheduler.add_job(
        job_chat_activity_reminder,
        CronTrigger(day_of_week="tue", hour=10, minute=0),
        id="chat_activity_reminder",
        replace_existing=True,
    )
    # Повторяющиеся задачи — каждый день в 08:30
    scheduler.add_job(
        job_recurring_tasks,
        CronTrigger(hour=8, minute=30),
        id="recurring_tasks",
        replace_existing=True,
    )
    # Pre-meeting brief — каждые 30 минут
    scheduler.add_job(
        job_pre_meeting_brief,
        CronTrigger(minute="0,30"),
        id="pre_meeting_brief",
        replace_existing=True,
    )
    # Напоминание о фолоуапе — каждый час в :05
    scheduler.add_job(
        job_followup_reminder,
        CronTrigger(minute=5),
        id="followup_reminder",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: 9 jobs active — morning_plan, weekly_digest, mr_sync, "
        "auto_checkup, health_score, metrics_degradation, qbr_cycle, personal_digest, "
        "chat_reminder, recurring_tasks, pre_meeting_brief, followup_reminder"
    )
