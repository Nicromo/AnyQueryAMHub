"""
Планировщик — APScheduler с интеграциями
Автоматические задачи:
  - 08:00 ежедн.  — проверка просроченных чекапов
  - 09:00 пн-пт  — утренний план + оповещения о встречах сегодня
  - 17:00 пт     — еженедельный дайджест
  - каждый час   — синхронизация данных
"""

import os
import logging
from datetime import date, datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

MSK = timezone(timedelta(hours=3))

# ============================================================================
# HELPER: Отправка Telegram-уведомлений
# ============================================================================

async def send_telegram(chat_id: int, text: str) -> bool:
    """Отправить сообщение в Telegram."""
    token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
        return resp.status_code == 200
    except Exception:
        return False


# ============================================================================
# JOBS
# ============================================================================

async def job_sync_airtable_clients():
    """Каждый час: синхронизировать клиентов из Airtable."""
    logger.info("🔄 Syncing Airtable clients...")
    try:
        from integrations.airtable import get_clients
        from database import SessionLocal
        from models import Client, SyncLog
        db = SessionLocal()
        sync_log = SyncLog(integration="airtable", resource_type="clients", action="sync", status="in_progress")
        try:
            at_clients = await get_clients(use_cache=False)
            synced = 0
            for ac in at_clients:
                c = db.query(Client).filter(Client.airtable_record_id == ac.get("id")).first()
                if not c:
                    c = Client(airtable_record_id=ac.get("id"), name=ac.get("name", ""),
                               manager_email=ac.get("manager"), segment=ac.get("segment"))
                    db.add(c)
                else:
                    c.name = ac.get("name", c.name)
                    c.manager_email = ac.get("manager", c.manager_email)
                    c.segment = ac.get("segment", c.segment)
                synced += 1
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = synced
            logger.info(f"✅ Airtable synced {synced} clients")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
            logger.error(f"❌ Airtable sync error: {e}")
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Airtable job error: {e}")


async def job_sync_merchrules_analytics():
    """Каждый час: синхронизировать аналитику из Merchrules."""
    logger.info("🔄 Syncing Merchrules analytics...")
    try:
        from database import SessionLocal
        from models import Client, SyncLog, CheckUp
        db = SessionLocal()
        sync_log = SyncLog(integration="merchrules", resource_type="analytics", action="sync", status="in_progress")
        try:
            from merchrules_sync import get_client_metrics
            clients = db.query(Client).filter(Client.merchrules_account_id != None).all()
            updated = 0
            for c in clients:
                metrics = await get_client_metrics(c.merchrules_account_id)
                if metrics and not metrics.get("error"):
                    updated += 1
                c.last_sync_at = datetime.utcnow()
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = updated
            logger.info(f"✅ Merchrules analytics synced {updated} clients")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
            logger.error(f"❌ Merchrules sync error: {e}")
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Merchrules job error: {e}")


async def job_sync_roadmap_tasks():
    """Каждый час: синхронизировать задачи."""
    logger.info("🔄 Syncing Roadmap tasks...")
    try:
        from database import SessionLocal
        from models import Client, Task, SyncLog
        from merchrules_sync import get_auth_token, fetch_site_tasks
        import httpx
        db = SessionLocal()
        sync_log = SyncLog(integration="merchrules", resource_type="tasks", action="sync", status="in_progress")
        try:
            clients = db.query(Client).filter(Client.merchrules_account_id != None).all()
            synced = 0
            async with httpx.AsyncClient(timeout=30) as hx:
                token = await get_auth_token(hx)
                if token:
                    headers = {"Authorization": f"Bearer {token}"}
                    for c in clients:
                        if not c.merchrules_account_id:
                            continue
                        tasks_data = await fetch_site_tasks(hx, headers, c.merchrules_account_id)
                        for t in tasks_data.get("tasks", []):
                            existing = db.query(Task).filter(Task.merchrules_task_id == str(t.get("id"))).first()
                            if not existing:
                                db.add(Task(
                                    client_id=c.id, merchrules_task_id=str(t.get("id")),
                                    title=t.get("title", ""), status=t.get("status", "plan"),
                                    priority=t.get("priority", "medium"), source="roadmap",
                                ))
                                synced += 1
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = synced
            logger.info(f"✅ Roadmap synced {synced} tasks")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
            logger.error(f"❌ Roadmap sync error: {e}")
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Roadmap job error: {e}")


async def job_sync_meetings():
    """Каждый час: синхронизировать встречи."""
    logger.info("🔄 Syncing meetings...")
    try:
        from database import SessionLocal
        from models import Client, Meeting, SyncLog
        from merchrules_sync import get_auth_token, fetch_site_meetings
        import httpx
        db = SessionLocal()
        sync_log = SyncLog(integration="merchrules", resource_type="meetings", action="sync", status="in_progress")
        try:
            clients = db.query(Client).filter(Client.merchrules_account_id != None).all()
            synced = 0
            async with httpx.AsyncClient(timeout=30) as hx:
                token = await get_auth_token(hx)
                if token:
                    headers = {"Authorization": f"Bearer {token}"}
                    for c in clients:
                        if not c.merchrules_account_id:
                            continue
                        mtg_data = await fetch_site_meetings(hx, headers, c.merchrules_account_id)
                        if mtg_data.get("last_meeting"):
                            c.last_meeting_date = datetime.fromisoformat(mtg_data["last_meeting"])
                            synced += 1
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = synced
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Meetings job error: {e}")


async def job_check_overdue_checkups():
    """Ежедневно 08:00: создать задачи на просроченные чекапы."""
    logger.info("🔔 Checking overdue checkups...")
    try:
        from database import SessionLocal
        from models import Client, Task, CheckUp, SyncLog
        db = SessionLocal()
        sync_log = SyncLog(integration="system", resource_type="checkups", action="check", status="in_progress")
        try:
            from models import CHECKUP_INTERVALS
            clients = db.query(Client).all()
            created = 0
            for c in clients:
                interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
                last = c.last_meeting_date or c.last_checkup
                if last and (datetime.utcnow() - last).days > interval:
                    existing = db.query(Task).filter(
                        Task.client_id == c.id, Task.source == "checkup",
                        Task.status.in_(["plan", "in_progress"])
                    ).first()
                    if not existing:
                        db.add(Task(
                            client_id=c.id, title=f"Чекап: {c.name}",
                            description=f"Последний контакт {(datetime.utcnow()-last).days} дн. назад (интервал {interval} дн.)",
                            status="plan", priority="high", source="checkup",
                        ))
                        c.needs_checkup = True
                        created += 1
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = created
            logger.info(f"✅ Created {created} checkup tasks")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Checkup job error: {e}")


async def job_morning_plan():
    """Ежедневно 09:00 пн-пт: утренний план + оповещения о встречах."""
    logger.info("📋 Morning plan + meeting alerts...")
    try:
        from database import SessionLocal
        from models import Client, Task, Meeting, User, CheckUp
        db = SessionLocal()
        now_msk = datetime.now(MSK)
        today = now_msk.date()
        tomorrow = today + timedelta(days=1)

        # Встречи сегодня
        meetings_today = db.query(Meeting).filter(
            Meeting.date >= datetime.combine(today, datetime.min.time()),
            Meeting.date < datetime.combine(tomorrow, datetime.min.time()),
        ).all()

        # Просроченные чекапы
        overdue = db.query(CheckUp).filter(CheckUp.status == "overdue").all()

        # Задачи на сегодня
        today_tasks = db.query(Task).filter(
            Task.due_date >= datetime.combine(today, datetime.min.time()),
            Task.due_date < datetime.combine(tomorrow, datetime.min.time()),
            Task.status.in_(["plan", "in_progress"]),
        ).all()

        # Отправляем каждому пользователю с telegram_id
        users = db.query(User).filter(User.telegram_id != None, User.is_active == True).all()
        for user in users:
            msg = f"☀️ <b>Доброе утро, {user.first_name or user.email}!</b>\n"
            msg += f"📅 {now_msk.strftime('%d.%m.%Y (%A)')}\n\n"

            # Встречи сегодня
            user_meetings = [m for m in meetings_today]
            if user.role == "manager":
                user_meetings = [m for m in meetings_today if m.client and m.client.manager_email == user.email]

            if user_meetings:
                msg += f"<b>📅 Встречи сегодня ({len(user_meetings)}):</b>\n"
                for m in user_meetings:
                    time_str = m.date.strftime("%H:%M") if m.date else "—"
                    client_name = m.client.name if m.client else "—"
                    link = m.recording_url or f"am-hub.ru/client/{m.client_id}"
                    msg += f"• <b>{time_str} МСК</b> — {client_name}\n  📎 <a href='{link}'>Ссылка на встречу</a>\n"
                msg += "\n"

            # Задачи
            user_tasks = today_tasks
            if user.role == "manager":
                user_tasks = [t for t in today_tasks if t.client and t.client.manager_email == user.email]
            if user_tasks:
                msg += f"<b>📋 Задачи на сегодня ({len(user_tasks)}):</b>\n"
                for t in user_tasks:
                    msg += f"• {t.title}\n"
                msg += "\n"

            # Просрочки
            if overdue:
                msg += f"<b>🔴 Просроченных чекапов: {len(overdue)}</b>\n"

            await send_telegram(int(user.telegram_id), msg)

        logger.info(f"✅ Morning plan sent to {len(users)} users")
        db.close()
    except Exception as e:
        logger.error(f"❌ Morning plan error: {e}")


async def job_weekly_digest():
    """Пятница 17:00: еженедельный дайджест."""
    logger.info("📊 Weekly digest...")
    try:
        from database import SessionLocal
        from models import Client, Task, Meeting, User
        db = SessionLocal()
        week_ago = datetime.utcnow() - timedelta(days=7)

        total_tasks = db.query(Task).count()
        done_tasks = db.query(Task).filter(Task.status == "done").count()
        week_tasks = db.query(Task).filter(Task.created_at >= week_ago).count()
        week_meetings = db.query(Meeting).filter(Meeting.date >= week_ago).count()
        clients = db.query(Client).count()

        users = db.query(User).filter(User.telegram_id != None, User.is_active == True).all()
        for user in users:
            msg = f"📊 <b>Еженедельный дайджест AM Hub</b>\n\n"
            msg += f"👥 Клиентов: {clients}\n"
            msg += f"📋 Задач всего: {total_tasks} (выполнено: {done_tasks})\n"
            msg += f"📅 За неделю: {week_tasks} задач, {week_meetings} встреч\n"
            msg += f"\nХороших выходных! 🎉"
            await send_telegram(int(user.telegram_id), msg)

        logger.info(f"✅ Weekly digest sent to {len(users)} users")
        db.close()
    except Exception as e:
        logger.error(f"❌ Weekly digest error: {e}")


# ============================================================================
# START
# ============================================================================

def start_scheduler():
    """Регистрировать и запустить все задачи."""

    # Hourly sync
    scheduler.add_job(job_sync_airtable_clients, "interval", hours=1,
                      id="sync_airtable", name="Sync Airtable Clients", replace_existing=True)
    scheduler.add_job(job_sync_merchrules_analytics, "interval", hours=1,
                      id="sync_mr_analytics", name="Sync Merchrules Analytics", replace_existing=True)
    scheduler.add_job(job_sync_roadmap_tasks, "interval", hours=1,
                      id="sync_roadmap", name="Sync Roadmap Tasks", replace_existing=True)
    scheduler.add_job(job_sync_meetings, "interval", hours=1,
                      id="sync_meetings", name="Sync Meetings", replace_existing=True)

    # Daily
    scheduler.add_job(job_check_overdue_checkups, "cron", hour=8, minute=0,
                      id="check_overdue", name="Check Overdue Checkups", replace_existing=True)
    scheduler.add_job(job_morning_plan, "cron", hour=9, minute=0, day_of_week="mon-fri",
                      id="morning_plan", name="Morning Plan + Meeting Alerts", replace_existing=True)

    # Weekly
    scheduler.add_job(job_weekly_digest, "cron", hour=17, minute=0, day_of_week="fri",
                      id="weekly_digest", name="Weekly Digest", replace_existing=True)

    scheduler.start()
    logger.info(f"✅ Scheduler started with {len(scheduler.get_jobs())} jobs")


if __name__ == "__main__":
    import logging, asyncio
    logging.basicConfig(level=logging.INFO)
    start_scheduler()
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        scheduler.shutdown()
