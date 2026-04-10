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


# ── Новые задачи автоматизации ────────────────────────────────────────────────

async def job_meeting_day_client_reminder():
    """
    Б3: Каждый день в 09:00 — отправляем напоминание клиенту в TG
    о запланированной сегодня встрече.
    """
    try:
        from database import get_clients_with_meeting_today
        from tg_bot import send_message

        clients_today = get_clients_with_meeting_today()
        if not clients_today:
            return

        for c in clients_today:
            tg_chat = c.get("tg_chat_id", "").strip()
            if not tg_chat:
                continue  # Нет TG-канала клиента

            meeting_time = c.get("meeting_time", "")
            time_str = f" в {meeting_time}" if meeting_time else ""

            msg = (
                f"👋 Привет!\n\n"
                f"Напоминаю: сегодня{time_str} у нас запланирована встреча.\n"
                f"Мы всё подготовили — ждём вас! 🚀\n\n"
                f"<i>С уважением, команда AnyQuery</i>"
            )
            await send_message(tg_chat, msg)
            logger.info("Meeting day reminder sent to client %s (chat %s)", c["name"], tg_chat)

    except Exception as exc:
        logger.error("job_meeting_day_client_reminder error: %s", exc)


async def job_risk_score_update():
    """
    В2: Каждый день в 06:00 — пересчёт Risk Score всех клиентов.
    При Risk Score > 70 — эскалация: задача + уведомление в TG.
    """
    try:
        from database import get_all_clients, update_client_risk_score, create_internal_task
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        clients = get_all_clients()
        escalations = []

        for c in clients:
            result = update_client_risk_score(c["id"])
            if result["score"] >= 70:
                # Создаём задачу если ещё нет
                from database import get_client_tasks
                open_tasks = get_client_tasks(c["id"], "open")
                has_risk_task = any(
                    "риск оттока" in t["text"].lower() or "risk score" in t["text"].lower()
                    for t in open_tasks
                )
                if not has_risk_task:
                    reasons_text = " | ".join(result.get("reasons", []))
                    create_internal_task(
                        client_id=c["id"],
                        text=f"🚨 Высокий Risk Score: {result['score']}/100 — {c['segment']}",
                        internal_note=f"Автозадача. Причины: {reasons_text}. Провести внеплановый контакт!"
                    )

                escalations.append({
                    "name": c["name"],
                    "segment": c["segment"],
                    "score": result["score"],
                    "level": result["level"],
                    "reasons": result.get("reasons", []),
                })

        if escalations and chat_id:
            lines = [f"🚨 <b>Высокий риск оттока ({len(escalations)} клиентов)</b>\n"]
            for e in escalations[:10]:
                reasons = " • " + " • ".join(e["reasons"][:2]) if e["reasons"] else ""
                lines.append(f"• <b>{e['name']}</b> [{e['segment']}] Risk={e['score']}/100{reasons}")
            lines.append("\n👉 AM Hub → карточка клиента → Risk Score")
            await send_message(chat_id, "\n".join(lines))

        logger.info("Risk score updated: %d escalations", len(escalations))

    except Exception as exc:
        logger.error("job_risk_score_update error: %s", exc)


async def job_platform_audit():
    """
    А3: 1-е число каждого месяца — аудит платформы всех ENT/SME+ клиентов.
    Проверяет конфиг поиска через Merchrules API → создаёт задачи для проблем.
    """
    try:
        from database import get_all_clients, create_internal_task, log_platform_audit
        from ai_followup import generate_platform_audit_tasks
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        clients = get_all_clients()
        audit_clients = [c for c in clients if c["segment"] in ("ENT", "SME+", "SME")]
        total_tasks = 0

        for c in audit_clients:
            site_ids = c.get("site_ids", "").strip()
            if not site_ids:
                continue

            # Простой аудит на основе доступных данных
            issues = []
            metrics_lines = []

            # Проверяем количество встреч за последние 90 дней
            from database import get_client_meetings
            meetings = get_client_meetings(c["id"], limit=10)
            from datetime import datetime as dt2
            recent_meetings = [
                m for m in meetings
                if m.get("meeting_date", "") >= (date.today() - timedelta(days=90)).isoformat()
            ]

            # Анализируем задачи
            from database import get_client_tasks
            open_tasks = get_client_tasks(c["id"], "open")
            blocked_tasks = [t for t in open_tasks if t.get("status") == "blocked"]
            overdue_tasks = [t for t in open_tasks if t.get("due_date", "") < date.today().isoformat()]

            if not recent_meetings:
                issues.append({"type": "no_meetings", "severity": "high",
                               "description": "Нет встреч за 90 дней"})
                metrics_lines.append("• Нет встреч за 90 дней")

            if len(blocked_tasks) > 2:
                issues.append({"type": "many_blocked", "severity": "medium",
                               "description": f"{len(blocked_tasks)} заблокированных задач"})
                metrics_lines.append(f"• {len(blocked_tasks)} заблокированных задач")

            if len(overdue_tasks) > 3:
                issues.append({"type": "many_overdue", "severity": "high",
                               "description": f"{len(overdue_tasks)} просроченных задач"})
                metrics_lines.append(f"• {len(overdue_tasks)} просроченных задач")

            if not issues:
                continue

            # Сохраняем аудит
            log_platform_audit(c["id"], issues)

            # AI генерация задач
            metrics_summary = "\n".join(metrics_lines)
            ai_tasks = await generate_platform_audit_tasks(c, site_ids.split(",")[0], metrics_summary)

            # Создаём задачи
            for task_data in ai_tasks[:5]:
                create_internal_task(
                    client_id=c["id"],
                    text=f"🔍 [Аудит] {task_data['text'][:200]}",
                    internal_note=f"Автоаудит. Причина: {task_data.get('reason', '')}. Команда: {task_data.get('team', 'CS')}"
                )
                total_tasks += 1

        if total_tasks and chat_id:
            await send_message(
                chat_id,
                f"🔍 <b>Ежемесячный аудит платформы завершён</b>\n\n"
                f"Проверено клиентов: {len(audit_clients)}\n"
                f"Создано задач: {total_tasks}\n\n"
                f"👉 AM Hub → Внутренние задачи"
            )

        logger.info("Platform audit done: %d tasks created", total_tasks)

    except Exception as exc:
        logger.error("job_platform_audit error: %s", exc)


async def job_nightly_problem_detection():
    """
    В1: Ночной детектор проблем (01:00).
    Анализирует деградацию метрик и Health Score.
    Создаёт задачи и отправляет алёрт AM.
    """
    try:
        from database import get_all_clients, get_health_history, create_internal_task, get_client_tasks
        from tg_bot import send_message
        from datetime import datetime as dt2

        chat_id = get_notify_chat_id()
        clients = get_all_clients()
        problems = []

        for c in clients:
            client_problems = []

            # Проверяем резкое падение Health Score за 3 дня
            history = get_health_history(c["id"], months=1)
            if len(history) >= 2:
                latest = history[-1]
                three_days_ago = next(
                    (h for h in reversed(history[:-1])
                     if h["snapshot_date"] <= (date.today() - timedelta(days=3)).isoformat()),
                    None
                )
                if three_days_ago:
                    drop = three_days_ago["health_score"] - latest["health_score"]
                    if drop >= 15:
                        client_problems.append(
                            f"Health Score: {three_days_ago['health_score']} → {latest['health_score']} (-{drop} за 3 дня)"
                        )

            # Проверяем много заблокированных задач (появились новые)
            open_tasks = get_client_tasks(c["id"], "open")
            new_blocked = [
                t for t in open_tasks
                if t.get("status") == "blocked"
                and t.get("created_at", "")[:10] == date.today().isoformat()
            ]
            if len(new_blocked) > 0:
                client_problems.append(f"Новые заблокированные задачи: {len(new_blocked)}")

            if client_problems:
                problems.append({
                    "name": c["name"],
                    "segment": c["segment"],
                    "id": c["id"],
                    "issues": client_problems,
                })
                # Создаём задачу
                for issue in client_problems:
                    create_internal_task(
                        client_id=c["id"],
                        text=f"⚠️ [Ночной детектор] {issue[:150]}",
                        internal_note="Автообнаружение проблемы. Проверить и принять меры."
                    )

        if problems and chat_id:
            lines = [f"🌙 <b>Ночной детектор: {len(problems)} проблем</b>\n"]
            for p in problems[:8]:
                lines.append(f"• <b>{p['name']}</b> [{p['segment']}]:")
                for issue in p["issues"]:
                    lines.append(f"  — {issue}")
            lines.append("\n👉 AM Hub → Внутренние задачи")
            await send_message(chat_id, "\n".join(lines))

        logger.info("Nightly problem detection: %d problems found", len(problems))

    except Exception as exc:
        logger.error("job_nightly_problem_detection error: %s", exc)


async def job_quarterly_benchmark():
    """
    Е2: 1-е число квартала — сравнение клиентов с медианой сегмента.
    Отправляет каждому клиенту (у кого есть TG) бенчмарк-сообщение.
    """
    try:
        from database import get_all_clients, calculate_health_score, get_segment_health_median
        from ai_followup import generate_benchmark_report
        from tg_bot import send_message

        # Пересчёт медиан сначала
        clients = get_all_clients()
        for c in clients:
            from database import update_client_health_score
            update_client_health_score(c["id"])

        medians = get_segment_health_median()
        chat_id = get_notify_chat_id()
        sent = 0

        for c in clients:
            tg_chat = c.get("tg_chat_id", "").strip()
            if not tg_chat:
                continue  # Нет TG-канала клиента

            health = calculate_health_score(c["id"])
            segment = c["segment"]
            median = medians.get(segment, 50)

            report = await generate_benchmark_report(c, health, median, segment)
            if report:
                header = f"📊 <b>Квартальный бенчмарк: {c['name']}</b>\n\n"
                await send_message(tg_chat, header + report)
                sent += 1

        if chat_id and sent:
            await send_message(
                chat_id,
                f"📊 <b>Квартальный бенчмарк отправлен</b>\n"
                f"Клиентов получили отчёт: {sent}\n"
                f"Медианы по сегментам: {medians}"
            )

        logger.info("Quarterly benchmark sent to %d clients", sent)

    except Exception as exc:
        logger.error("job_quarterly_benchmark error: %s", exc)


# ── Ж3: Годовщины клиентов ───────────────────────────────────────────────────

async def job_client_anniversary_check():
    """
    Ж3: Ежедневно проверяет годовщины клиентов (1/2/3/5 лет).
    Отправляет AM черновик поздравления в TG.
    """
    try:
        from database import get_clients_with_anniversary_soon
        from ai_followup import generate_anniversary_message
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        clients = get_clients_with_anniversary_soon(days_ahead=5)
        if not clients or not chat_id:
            return

        for c in clients:
            years = c["anniversary_years"]
            days_left = c["anniversary_days_left"]
            draft = await generate_anniversary_message(c, years)

            prefix = "🎉 Сегодня!" if days_left == 0 else f"⏰ Через {days_left} дн."
            msg = (
                f"🎂 <b>Годовщина клиента</b> · {prefix}\n\n"
                f"<b>{c['name']}</b> [{c['segment']}] — {years} {'год' if years == 1 else 'года' if years < 5 else 'лет'} с AnyQuery\n"
                f"Дата: {c['anniversary_date']}\n\n"
                f"<b>Черновик поздравления:</b>\n{draft}\n\n"
                f"👉 Отправь клиенту в TG или адаптируй по своему"
            )
            await send_message(chat_id, msg)
            logger.info("Anniversary alert: %s (%d years)", c["name"], years)

    except Exception as exc:
        logger.error("job_client_anniversary_check error: %s", exc)


# ── Ж4: Welcome-sequence для новых клиентов ──────────────────────────────────

async def job_welcome_sequence():
    """
    Ж4: Ежедневно проверяет новых клиентов и отправляет welcome-сообщение.
    Также авто-создаёт онбординг-задачи.
    """
    try:
        from database import get_new_clients_for_welcome, mark_welcome_sent, create_internal_task, get_task_templates
        from ai_followup import generate_welcome_message
        from tg_bot import send_message

        clients = get_new_clients_for_welcome()
        if not clients:
            return

        # Найдём шаблон онбординга
        templates = get_task_templates()
        onboarding_tmpl = next((t for t in templates if "онбординг" in t["name"].lower()), None)

        for c in clients:
            # Отправляем welcome в TG-канал клиента
            if c.get("tg_chat_id"):
                draft = await generate_welcome_message(c)
                await send_message(c["tg_chat_id"], draft)

            # Создаём онбординг-задачи если есть шаблон
            if onboarding_tmpl:
                import json
                from datetime import date, timedelta
                tasks_raw = onboarding_tmpl.get("tasks", [])
                for task_data in tasks_raw:
                    days = task_data.get("days", 7)
                    due = (date.today() + timedelta(days=days)).isoformat()
                    create_internal_task(
                        client_id=c["id"],
                        text=task_data.get("text", "Онбординг задача"),
                        due_date=due,
                        internal_note=f"Авто-создана при онбординге нового клиента"
                    )

            mark_welcome_sent(c["id"])
            logger.info("Welcome sequence sent for client %s", c["name"])

    except Exception as exc:
        logger.error("job_welcome_sequence error: %s", exc)


# ── З2: AI-приоритизация задач ────────────────────────────────────────────────

async def job_ai_task_prioritization():
    """
    З2: Каждое утро 07:30 — AI расставляет приоритеты задач и отправляет список AM.
    """
    try:
        from database import get_all_tasks, get_all_manager_tg_ids, get_manager_client_ids, get_client_tasks, get_client, checkup_status
        from ai_followup import prioritize_tasks_ai
        from tg_bot import send_message
        from datetime import date

        manager_ids = get_all_manager_tg_ids()
        today = date.today().isoformat()

        for tg_id in manager_ids:
            client_ids = get_manager_client_ids(tg_id)
            if not client_ids:
                continue

            # Собираем все открытые задачи менеджера
            all_tasks = []
            for cid in client_ids:
                client = get_client(cid)
                if not client:
                    continue
                tasks = get_client_tasks(cid, "open")
                for t in tasks:
                    t["client_name"] = client["name"]
                    t["segment"] = client["segment"]
                all_tasks.extend(tasks)

            if not all_tasks:
                continue

            # AI приоритизация
            prioritized = await prioritize_tasks_ai(all_tasks, [])

            # Топ-10 задач для AM
            top = prioritized[:10]
            lines = [f"🧠 <b>AI-приоритеты на {today}</b>\n"]
            for i, t in enumerate(top, 1):
                due = f" · {t.get('due_date', '')[:10]}" if t.get("due_date") else ""
                reason = f"\n   <i>{t.get('priority_reason', '')}</i>" if t.get("priority_reason") else ""
                status_icon = "🔴" if t.get("status") == "blocked" else "📋"
                lines.append(f"{i}. {status_icon} <b>{t.get('client_name', '?')}</b>{due}\n   {t.get('text', '')[:80]}{reason}")

            lines.append("\n<i>Порядок определён AI на основе сегмента, дедлайнов и рисков</i>")
            await send_message(tg_id, "\n".join(lines))
            logger.info("AI prioritization sent to manager %s", tg_id)

    except Exception as exc:
        logger.error("job_ai_task_prioritization error: %s", exc)


# ── З3: Кросс-клиентные паттерны ─────────────────────────────────────────────

async def job_cross_client_patterns():
    """
    З3: Каждую неделю — ищет одинаковые проблемы у 3+ клиентов.
    Сигнализирует о системных проблемах.
    """
    try:
        from database import get_common_task_patterns
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        if not chat_id:
            return

        patterns = get_common_task_patterns(days_back=7, min_count=3)
        if not patterns:
            return

        lines = [f"🔍 <b>Системные паттерны за неделю</b>\n"]
        lines.append("Одна и та же проблема у нескольких клиентов:\n")

        for p in patterns[:5]:
            clients_list = list({t["client_name"] for t in p["clients"]})[:5]
            lines.append(f"• <b>«{p['keyword']}»</b> — {p['count']} задач у {len(clients_list)} клиентов")
            lines.append(f"  Клиенты: {', '.join(clients_list)}")

        lines.append("\n💡 Возможно это системная проблема — стоит создать задачу для команды")
        await send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        logger.error("job_cross_client_patterns error: %s", exc)


# ── З4: Авто-закрытие/алерт по устаревшим задачам ────────────────────────────

async def job_stale_task_alert():
    """
    З4: Еженедельно — алерт по задачам открытым > 45 дней без движения.
    """
    try:
        from database import get_open_tasks_older_than
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        if not chat_id:
            return

        stale = get_open_tasks_older_than(days=45)
        if not stale:
            return

        lines = [f"⏳ <b>Зависшие задачи ({len(stale)} шт)</b>\n"]
        lines.append("Открыты более 45 дней без изменений:\n")

        # Группируем по клиентам
        by_client: dict = {}
        for t in stale:
            name = t.get("client_name", "?")
            by_client.setdefault(name, []).append(t)

        for client_name, tasks in list(by_client.items())[:8]:
            lines.append(f"• <b>{client_name}</b>:")
            for t in tasks[:2]:
                age = t.get("created_at", "")[:10]
                lines.append(f"  — #{t['id']} {t['text'][:60]} (с {age})")

        lines.append("\n👉 AM Hub → Задачи → проверь и закрой или обнови")
        await send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        logger.error("job_stale_task_alert error: %s", exc)


# ── К1: Мониторинг метрик поиска ─────────────────────────────────────────────

async def job_search_metrics_monitor():
    """
    К1: Еженедельно — проверяет метрики поиска через Merchrules /analytics/full.
    Создаёт диагностические задачи при падении CTR/конверсии > 15%.
    """
    try:
        from database import get_all_clients, create_internal_task
        from tg_bot import send_message
        import httpx, os

        chat_id = get_notify_chat_id()
        mr_base = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
        mr_token = os.getenv("MERCHRULES_TOKEN", "")

        if not mr_token:
            logger.info("job_search_metrics_monitor: MERCHRULES_TOKEN not set, skipping")
            return

        clients = get_all_clients()
        alerts = []

        async with httpx.AsyncClient(timeout=30) as hx:
            for c in clients:
                site_ids = c.get("site_ids", "").strip()
                if not site_ids:
                    continue
                site_id = site_ids.split(",")[0].strip()

                try:
                    resp = await hx.get(
                        f"{mr_base}/analytics/full",
                        headers={"Authorization": f"Bearer {mr_token}"},
                        params={"site_id": site_id, "period": "week"}
                    )
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    # Ожидаем: { "ctr": float, "conversion": float, "prev_ctr": float, "prev_conversion": float }
                    ctr = data.get("ctr", 0)
                    prev_ctr = data.get("prev_ctr", 0)
                    conv = data.get("conversion", 0)
                    prev_conv = data.get("prev_conversion", 0)

                    issues = []
                    if prev_ctr > 0 and (prev_ctr - ctr) / prev_ctr > 0.15:
                        issues.append(f"CTR упал: {prev_ctr:.1%} → {ctr:.1%}")
                    if prev_conv > 0 and (prev_conv - conv) / prev_conv > 0.15:
                        issues.append(f"Конверсия упала: {prev_conv:.1%} → {conv:.1%}")

                    for issue in issues:
                        create_internal_task(
                            client_id=c["id"],
                            text=f"📉 [Метрики] {issue}",
                            internal_note="Авто-детект. Проверить настройки поиска, бустинг, индекс."
                        )
                        alerts.append({"name": c["name"], "segment": c["segment"], "issue": issue})

                except Exception as e:
                    logger.debug("Metrics check failed for %s: %s", c["name"], e)
                    continue

        if alerts and chat_id:
            lines = [f"📉 <b>Падение метрик поиска ({len(alerts)})</b>\n"]
            for a in alerts[:8]:
                lines.append(f"• <b>{a['name']}</b> [{a['segment']}]: {a['issue']}")
            lines.append("\n👉 AM Hub → Внутренние задачи")
            await send_message(chat_id, "\n".join(lines))

    except Exception as exc:
        logger.error("job_search_metrics_monitor error: %s", exc)


# ── К2: AI-рекомендации синонимов ────────────────────────────────────────────

async def job_ai_synonym_recommendations():
    """
    К2: Еженедельно — анализирует настройки поиска через Merchrules /search-settings,
    генерирует AI-рекомендации по синонимам.
    """
    try:
        from database import get_all_clients, create_internal_task
        from ai_followup import generate_synonym_recommendations
        from tg_bot import send_message
        import httpx, os

        chat_id = get_notify_chat_id()
        mr_base = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
        mr_token = os.getenv("MERCHRULES_TOKEN", "")

        if not mr_token:
            logger.info("job_ai_synonym_recommendations: MERCHRULES_TOKEN not set, skipping")
            return

        clients = [c for c in __import__("database").get_all_clients()
                   if c["segment"] in ("ENT", "SME+", "SME")][:10]  # приоритет крупным

        created = 0
        async with httpx.AsyncClient(timeout=30) as hx:
            for c in clients:
                site_ids = c.get("site_ids", "").strip()
                if not site_ids:
                    continue
                site_id = site_ids.split(",")[0].strip()

                try:
                    resp = await hx.get(
                        f"{mr_base}/search-settings",
                        headers={"Authorization": f"Bearer {mr_token}"},
                        params={"site_id": site_id}
                    )
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    zero_results = data.get("zero_result_queries", [])[:20]
                    top_queries = data.get("top_queries", [])[:20]

                    if not zero_results:
                        continue

                    recs = await generate_synonym_recommendations(c, zero_results, top_queries)
                    if recs:
                        summary = "; ".join(f"{r['query']} → {', '.join(r['synonyms'][:2])}" for r in recs[:3])
                        create_internal_task(
                            client_id=c["id"],
                            text=f"💡 [AI] Рекомендации синонимов: {summary[:150]}",
                            internal_note=f"AI-анализ {len(zero_results)} запросов без результатов. Полный список в задаче."
                        )
                        created += 1

                except Exception as e:
                    logger.debug("Synonym check failed for %s: %s", c["name"], e)
                    continue

        if created and chat_id:
            await send_message(chat_id, f"💡 <b>AI-синонимы</b>: создано {created} задач с рекомендациями\n👉 AM Hub → Внутренние задачи")

    except Exception as exc:
        logger.error("job_ai_synonym_recommendations error: %s", exc)


# ── К3: Сезонные чеклисты ────────────────────────────────────────────────────

async def job_seasonal_checklists():
    """
    К3: Ежемесячно — проверяет ближайшие сезонные события и создаёт задачи для ENT/SME+.
    """
    try:
        from database import get_all_clients, create_internal_task, get_upcoming_seasonal_events
        from tg_bot import send_message

        chat_id = get_notify_chat_id()
        events = get_upcoming_seasonal_events(days_ahead=21)
        if not events:
            return

        clients = [c for c in __import__("database").get_all_clients()
                   if c["segment"] in ("ENT", "SME+", "SME")]
        created = 0

        for event in events:
            for c in clients:
                for task_text in event["tasks"]:
                    # Проверяем нет ли уже такой задачи
                    from database import get_client_tasks
                    open_tasks = get_client_tasks(c["id"], "open")
                    if any(task_text[:40] in t["text"] for t in open_tasks):
                        continue
                    from datetime import date, timedelta
                    due = (date.today() + timedelta(days=event["days_left"] - 7)).isoformat()
                    create_internal_task(
                        client_id=c["id"],
                        text=f"🗓️ [{event['name']}] {task_text}",
                        due_date=due,
                        internal_note=f"Авто-сезонная задача. Событие: {event['name']} {event['date']}"
                    )
                    created += 1

        if created and chat_id:
            event_names = " / ".join(e["name"] for e in events)
            await send_message(
                chat_id,
                f"🗓️ <b>Сезонные задачи</b>: создано {created} задач\n"
                f"События: {event_names}\n👉 AM Hub → Внутренние задачи"
            )
        logger.info("Seasonal checklists: %d tasks created for %d events", created, len(events))

    except Exception as exc:
        logger.error("job_seasonal_checklists error: %s", exc)


# ── Л2: Airtable health sync ─────────────────────────────────────────────────

async def job_airtable_health_sync():
    """
    Л2: Ежедневно — пушит health_score и risk_score всех клиентов в Airtable.
    """
    try:
        from database import get_all_clients, calculate_health_score, calculate_risk_score
        from airtable_sync import sync_health_to_airtable

        clients = get_all_clients()
        synced = 0
        for c in clients:
            health = calculate_health_score(c["id"])
            risk = calculate_risk_score(c["id"])
            ok = await sync_health_to_airtable(
                client_name=c["name"],
                health_score=health["score"],
                health_color=health["color"],
                risk_score=risk["score"],
                risk_level=risk["level"],
            )
            if ok:
                synced += 1

        logger.info("Airtable health sync: %d/%d clients updated", synced, len(clients))

    except Exception as exc:
        logger.error("job_airtable_health_sync error: %s", exc)


# ── AR sync — Дебиторская задолженность ──────────────────────────────────────

async def job_ar_sync():
    """
    Ежедневно — синхронизирует задолженность из Airtable → БД.
    Алертит AM если просрочка > 30 дней.
    """
    try:
        from database import get_all_clients, update_client_ar, get_am_setting, get_all_manager_tg_ids
        from airtable_sync import sync_ar_from_airtable
        from tg_bot import send_message

        clients = get_all_clients()
        ar_data = await sync_ar_from_airtable(clients)

        for item in ar_data:
            update_client_ar(item["client_id"], item["amount"], item["days_overdue"])

        # Алерт по критичным просрочкам (> 30 дней)
        critical = [x for x in ar_data if x["days_overdue"] >= 30]
        if not critical:
            return

        chat_id = get_notify_chat_id()
        if not chat_id:
            return

        lines = [f"💰 <b>Дебиторская задолженность — просрочка!</b>\n"]
        for item in sorted(critical, key=lambda x: -x["days_overdue"])[:10]:
            days_icon = "🔴" if item["days_overdue"] >= 60 else "🟡"
            amount_str = f"{item['amount']:,.0f} ₽" if item["amount"] else "сумма неизвестна"
            lines.append(
                f"{days_icon} <b>{item['name']}</b>: {amount_str}, "
                f"просрочка {item['days_overdue']} дн."
            )
        lines.append("\n👉 AM Hub → клиент → обсудить оплату")
        await send_message(chat_id, "\n".join(lines))
        logger.info("AR sync: %d clients with AR, %d critical", len(ar_data), len(critical))

    except Exception as exc:
        logger.error("job_ar_sync error: %s", exc)


# ── Тикеты TBank Time → задачи AM ────────────────────────────────────────────

async def job_time_tickets_to_tasks():
    """
    Ежедневно — получает тикеты из TBank Time.
    Если тикет открыт > 3 дней → создаёт задачу AM.
    """
    try:
        from database import (
            get_all_clients, create_internal_task,
            get_old_open_tickets, mark_ticket_task_created,
        )
        from tbank_time import sync_tickets_to_db
        from tg_bot import send_message

        clients = get_all_clients()
        await sync_tickets_to_db(clients)

        old_tickets = get_old_open_tickets(days=3)
        created = 0

        for ticket in old_tickets:
            client_id = ticket.get("client_id")
            if not client_id:
                continue

            priority_tag = "🔴" if ticket["priority"] in ("high", "urgent", "critical") else "🟡"
            task_text = (
                f"{priority_tag} [Тикет {ticket['days_open']}д] {ticket['subject'][:100]}"
            )
            create_internal_task(
                client_id=client_id,
                text=task_text,
                internal_note=f"Тикет из TBank Time. Открыт {ticket['days_open']} дней. {ticket.get('url', '')}",
            )
            mark_ticket_task_created(ticket["id"])
            created += 1

        if created:
            chat_id = get_notify_chat_id()
            if chat_id:
                await send_message(
                    chat_id,
                    f"🎫 <b>Тикеты без ответа ({created})</b>\n"
                    f"Открыты более 3 дней → создано задач AM\n"
                    f"👉 AM Hub → Внутренние задачи"
                )
            logger.info("Time tickets: %d tasks created", created)

    except Exception as exc:
        logger.error("job_time_tickets_to_tasks error: %s", exc)


# ── Апсел-сигналы ─────────────────────────────────────────────────────────────

async def job_upsell_detection():
    """
    Еженедельно — детектирует апсел-сигналы по каждому клиенту.
    Создаёт задачи AM если найдены возможности.
    """
    try:
        from database import (
            get_all_clients, get_client_tasks, save_upsell_signal,
            get_client_upsell_signals, create_internal_task,
        )
        from ai_followup import detect_upsell_signals
        from merchrules_sync import get_client_mr_data
        from tg_bot import send_message
        import os

        chat_id = get_notify_chat_id()
        mr_token = os.getenv("MERCHRULES_TOKEN", "")

        clients = get_all_clients()
        found_total = 0

        for c in clients:
            if c["segment"] not in ("ENT", "SME+", "SME"):
                continue

            tasks = get_client_tasks(c["id"], "open")
            mr_data = {}
            if mr_token and c.get("site_ids"):
                site_id = c["site_ids"].split(",")[0].strip()
                mr_data = await get_client_mr_data(site_id) or {}

            # Проверяем нет ли уже открытых сигналов
            existing = get_client_upsell_signals(c["id"])
            if any(s["status"] == "open" for s in existing):
                continue  # Уже есть открытый сигнал

            signals = await detect_upsell_signals(c, mr_data, tasks)
            for sig in signals:
                if sig.get("confidence", 0) < 0.65:
                    continue
                signal_id = save_upsell_signal(c["id"], sig["type"], sig["details"])
                create_internal_task(
                    client_id=c["id"],
                    text=f"📈 [Апсел] {sig['details'][:120]}",
                    internal_note=f"Авто-детект. Тип: {sig['type']}. Уверенность: {sig['confidence']:.0%}",
                )
                found_total += 1

        if found_total and chat_id:
            await send_message(
                chat_id,
                f"📈 <b>Апсел-сигналы</b>: найдено {found_total} возможностей\n"
                f"👉 AM Hub → Внутренние задачи"
            )
        logger.info("Upsell detection: %d signals found", found_total)

    except Exception as exc:
        logger.error("job_upsell_detection error: %s", exc)


# ── Автотеги клиентов ─────────────────────────────────────────────────────────

async def job_auto_tagging():
    """
    Еженедельно — AI расставляет теги всем клиентам на основе истории.
    """
    try:
        from database import (
            get_all_clients, get_client_tasks, get_client_meetings, set_client_tags,
        )
        from ai_followup import generate_client_tags

        clients = get_all_clients()
        tagged = 0

        for c in clients:
            try:
                tasks    = get_client_tasks(c["id"], "open")
                meetings = get_client_meetings(c["id"], limit=5)
                tags = await generate_client_tags(c, tasks, meetings)
                if tags:
                    set_client_tags(c["id"], tags, source="ai")
                    tagged += 1
            except Exception as e:
                logger.debug("Auto-tagging failed for %s: %s", c["name"], e)

        logger.info("Auto-tagging complete: %d clients tagged", tagged)

    except Exception as exc:
        logger.error("job_auto_tagging error: %s", exc)


async def job_mr_task_done_notify():
    """
    Б2: При синхронизации MR — уведомляем клиента в TG о закрытых задачах.
    Запускается каждый час вместе с MR sync.
    """
    try:
        from database import get_all_clients, get_client_tasks, update_task_status, get_client
        from tg_bot import send_message
        from merchrules_sync import sync_clients_from_merchrules, invalidate_cache

        invalidate_cache()
        clients = get_all_clients()
        mr_data = await sync_clients_from_merchrules(clients)

        if not mr_data:
            return

        open_tasks = get_client_tasks.__module__  # just to import

        from database import get_all_tasks
        all_open = get_all_tasks("open")

        notified_clients = set()

        for site_id, data in mr_data.items():
            mr_tasks = {t["title"].lower(): t["status"]
                        for t in data.get("tasks", []) if t.get("title")}

            for task in all_open:
                key = task["text"].lower()
                if key not in mr_tasks:
                    continue
                new_status = mr_tasks[key]
                if new_status not in ("done", "completed"):
                    continue

                # Задача закрыта — обновляем статус
                update_task_status(task["id"], "done")

                # Уведомляем клиента (один раз за сессию)
                client_id = task["client_id"]
                if client_id in notified_clients:
                    continue

                client = get_client(client_id)
                if not client:
                    continue

                tg_chat = client.get("tg_chat_id", "").strip()
                if not tg_chat:
                    continue

                task_text = task["text"][:100]
                msg = (
                    f"✅ <b>Задача выполнена!</b>\n\n"
                    f"«{task_text}»\n\n"
                    f"Задача закрыта в системе. Если есть вопросы — напишите нам!"
                )
                await send_message(tg_chat, msg)
                notified_clients.add(client_id)
                logger.info("Task done notification sent to client %s", client["name"])

    except Exception as exc:
        logger.error("job_mr_task_done_notify error: %s", exc)


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
    # Б3: Напоминание клиенту о встрече сегодня — 09:00
    scheduler.add_job(
        job_meeting_day_client_reminder,
        CronTrigger(hour=9, minute=0),
        id="meeting_day_client_reminder",
        replace_existing=True,
    )
    # В2: Обновление Risk Score — 06:00
    scheduler.add_job(
        job_risk_score_update,
        CronTrigger(hour=6, minute=0),
        id="risk_score_update",
        replace_existing=True,
    )
    # А3: Ежемесячный аудит платформы — 1-е число в 02:00
    scheduler.add_job(
        job_platform_audit,
        CronTrigger(day=1, hour=2, minute=0),
        id="platform_audit",
        replace_existing=True,
    )
    # В1: Ночной детектор проблем — 01:00
    scheduler.add_job(
        job_nightly_problem_detection,
        CronTrigger(hour=1, minute=0),
        id="nightly_problem_detection",
        replace_existing=True,
    )
    # Е2: Квартальный бенчмарк — 1 янв/апр/июл/окт в 09:00
    scheduler.add_job(
        job_quarterly_benchmark,
        CronTrigger(month="1,4,7,10", day=1, hour=9, minute=0),
        id="quarterly_benchmark",
        replace_existing=True,
    )
    # Б2: MR задача закрыта → уведомить клиента — каждый час в :15
    scheduler.add_job(
        job_mr_task_done_notify,
        CronTrigger(minute=15),
        id="mr_task_done_notify",
        replace_existing=True,
    )
    # Ж3: Годовщины клиентов — каждый день в 09:15
    scheduler.add_job(
        job_client_anniversary_check,
        CronTrigger(hour=9, minute=15),
        id="client_anniversary_check",
        replace_existing=True,
    )
    # Ж4: Welcome-последовательность — каждый день в 10:00
    scheduler.add_job(
        job_welcome_sequence,
        CronTrigger(hour=10, minute=0),
        id="welcome_sequence",
        replace_existing=True,
    )
    # З2: AI-приоритизация задач — пн-пт в 07:30
    scheduler.add_job(
        job_ai_task_prioritization,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30),
        id="ai_task_prioritization",
        replace_existing=True,
    )
    # З3: Кросс-клиентные паттерны — понедельник в 09:00
    scheduler.add_job(
        job_cross_client_patterns,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="cross_client_patterns",
        replace_existing=True,
    )
    # З4: Алерт по устаревшим задачам — понедельник в 10:30
    scheduler.add_job(
        job_stale_task_alert,
        CronTrigger(day_of_week="mon", hour=10, minute=30),
        id="stale_task_alert",
        replace_existing=True,
    )
    # К1: Мониторинг метрик поиска — понедельник в 08:00
    scheduler.add_job(
        job_search_metrics_monitor,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="search_metrics_monitor",
        replace_existing=True,
    )
    # К2: AI-рекомендации синонимов — среда в 10:00
    scheduler.add_job(
        job_ai_synonym_recommendations,
        CronTrigger(day_of_week="wed", hour=10, minute=0),
        id="ai_synonym_recommendations",
        replace_existing=True,
    )
    # К3: Сезонные чеклисты — 1-е число каждого месяца в 08:00
    scheduler.add_job(
        job_seasonal_checklists,
        CronTrigger(day=1, hour=8, minute=0),
        id="seasonal_checklists",
        replace_existing=True,
    )
    # Л2: Airtable health/risk sync — каждый день в 05:00
    scheduler.add_job(
        job_airtable_health_sync,
        CronTrigger(hour=5, minute=0),
        id="airtable_health_sync",
        replace_existing=True,
    )
    # AR: Дебиторская задолженность из Airtable — каждый день в 05:30
    scheduler.add_job(
        job_ar_sync,
        CronTrigger(hour=5, minute=30),
        id="ar_sync",
        replace_existing=True,
    )
    # Тикеты TBank Time → задачи AM — каждый день в 08:45
    scheduler.add_job(
        job_time_tickets_to_tasks,
        CronTrigger(hour=8, minute=45),
        id="time_tickets_to_tasks",
        replace_existing=True,
    )
    # Апсел-сигналы — вторник в 09:00
    scheduler.add_job(
        job_upsell_detection,
        CronTrigger(day_of_week="tue", hour=9, minute=0),
        id="upsell_detection",
        replace_existing=True,
    )
    # Автотеги — воскресенье в 02:00
    scheduler.add_job(
        job_auto_tagging,
        CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="auto_tagging",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: 33 jobs active — morning_plan, weekly_digest, mr_sync, "
        "auto_checkup, health_score, metrics_degradation, qbr_cycle, personal_digest, "
        "chat_reminder, recurring_tasks, pre_meeting_brief, followup_reminder, "
        "meeting_day_client_reminder, risk_score_update, platform_audit, "
        "nightly_problem_detection, quarterly_benchmark, mr_task_done_notify, "
        "client_anniversary_check, welcome_sequence, ai_task_prioritization, "
        "cross_client_patterns, stale_task_alert, search_metrics_monitor, "
        "ai_synonym_recommendations, seasonal_checklists, airtable_health_sync, "
        "ar_sync, time_tickets_to_tasks, upsell_detection, auto_tagging"
    )
