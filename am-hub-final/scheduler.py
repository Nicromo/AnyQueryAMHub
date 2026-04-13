"""
Планировщик — APScheduler с интеграциями
Автоматические задачи:
  - 09:00 пн-пт  — утренний план (чекапы + задачи)
  - 17:00 пт     — еженедельный дайджест
  - каждый час   — синхронизация данных (Airtable + Merchrules)
  - каждые 30 мин — проверка напоминаний о встречах
  - 08:00 ежедн.  — автозадачи на чекапы
"""

import os
import asyncio
import logging
import httpx
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal
from models import Client, Task, Meeting, CheckUp, SyncLog
from integrations import airtable
from merchrules_sync import get_auth_token, fetch_site_tasks, fetch_site_meetings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


# ============================================================================
# SYNC JOBS
# ============================================================================


async def job_sync_airtable_clients():
    """Каждый час: синхронизировать клиентов из Airtable"""
    db = SessionLocal()
    sync_log = SyncLog(
        integration="airtable",
        resource_type="clients",
        action="sync",
        status="in_progress",
    )
    
    try:
        logger.info("🔄 Syncing Airtable clients...")
        airtable_clients = await airtable.get_clients(use_cache=False)
        
        synced = 0
        for at_client in airtable_clients:
            # Найти или создать
            client = db.query(Client).filter(
                Client.airtable_record_id == at_client['id']
            ).first()
            
            if not client:
                client = Client(
                    airtable_record_id=at_client['id'],
                    name=at_client['name'],
                    manager_email=at_client['manager'],
                    segment=at_client['segment'],
                )
                db.add(client)
            else:
                client.name = at_client['name']
                client.manager_email = at_client['manager']
                client.segment = at_client['segment']
            
            client.last_sync_at = datetime.utcnow()
            synced += 1
        
        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = synced
        logger.info(f"✅ Synced {synced} Airtable clients")
        
    except Exception as e:
        logger.error(f"❌ Airtable sync error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()


async def job_sync_merchrules_analytics():
    """Каждый час: синхронизировать данные задач и встреч из Merchrules"""
    import httpx
    db = SessionLocal()
    sync_log = SyncLog(
        integration="merchrules",
        resource_type="analytics",
        action="sync",
        status="in_progress",
    )

    try:
        logger.info("🔄 Syncing Merchrules analytics...")
        login = os.getenv("MERCHRULES_LOGIN", "")
        password = os.getenv("MERCHRULES_PASSWORD", "")
        if not login or not password:
            sync_log.status = "skipped"
            sync_log.message = "MERCHRULES_LOGIN/PASSWORD не заданы"
            return

        clients = db.query(Client).filter(Client.merchrules_account_id.isnot(None)).all()
        if not clients:
            sync_log.status = "success"
            sync_log.records_processed = 0
            return

        updated = 0
        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                sync_log.status = "error"
                sync_log.message = "Ошибка авторизации Merchrules"
                return
            headers = {"Authorization": f"Bearer {token}"}

            for client in clients:
                try:
                    tasks_data = await fetch_site_tasks(hx, headers, client.merchrules_account_id)
                    meetings_data = await fetch_site_meetings(hx, headers, client.merchrules_account_id)

                    open_count = tasks_data.get("open_tasks", 0)
                    if open_count < 10:
                        client.segment = client.segment or "SMB"
                    elif open_count < 30:
                        client.segment = client.segment or "SME"
                    else:
                        client.segment = client.segment or "ENT"

                    if meetings_data.get("last_meeting"):
                        try:
                            client.last_meeting_date = datetime.fromisoformat(meetings_data["last_meeting"])
                        except Exception:
                            pass

                    client.last_sync_at = datetime.utcnow()
                    updated += 1
                except Exception as exc:
                    logger.warning("MR analytics skip client %s: %s", client.id, exc)

        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = updated
        logger.info(f"✅ Updated {updated} clients from Merchrules")

    except Exception as e:
        logger.error(f"❌ Merchrules sync error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()


async def job_sync_roadmap_tasks():
    """Каждый час: синхронизировать задачи из Merchrules Roadmap"""
    import httpx
    db = SessionLocal()
    sync_log = SyncLog(
        integration="merchrules",
        resource_type="tasks",
        action="sync",
        status="in_progress",
    )

    try:
        logger.info("🔄 Syncing Roadmap tasks...")
        login = os.getenv("MERCHRULES_LOGIN", "")
        password = os.getenv("MERCHRULES_PASSWORD", "")
        if not login or not password:
            sync_log.status = "skipped"
            sync_log.message = "MERCHRULES_LOGIN/PASSWORD не заданы"
            return

        clients = db.query(Client).filter(Client.merchrules_account_id.isnot(None)).all()
        synced = 0

        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                sync_log.status = "error"
                sync_log.message = "Ошибка авторизации Merchrules"
                return
            headers = {"Authorization": f"Bearer {token}"}

            for client in clients:
                try:
                    tasks_data = await fetch_site_tasks(hx, headers, client.merchrules_account_id)
                    for task_data in tasks_data.get("tasks", []):
                        mr_id = str(task_data.get("id", ""))
                        if not mr_id:
                            continue
                        task = db.query(Task).filter(Task.merchrules_task_id == mr_id).first()
                        if not task:
                            db.add(Task(
                                client_id=client.id,
                                merchrules_task_id=mr_id,
                                title=task_data.get("title", ""),
                                status=task_data.get("status", "plan"),
                                priority=task_data.get("priority", "medium"),
                                source="roadmap",
                            ))
                            synced += 1
                        else:
                            task.status = task_data.get("status", task.status)
                            task.priority = task_data.get("priority", task.priority)
                except Exception as exc:
                    logger.warning("MR tasks skip client %s: %s", client.id, exc)

        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = synced
        logger.info(f"✅ Synced {synced} roadmap tasks")

    except Exception as e:
        logger.error(f"❌ Roadmap sync error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()


async def job_sync_meetings():
    """Каждый час: синхронизировать встречи из Merchrules"""
    import httpx
    db = SessionLocal()
    sync_log = SyncLog(
        integration="merchrules",
        resource_type="meetings",
        action="sync",
        status="in_progress",
    )

    try:
        logger.info("🔄 Syncing meetings...")
        login = os.getenv("MERCHRULES_LOGIN", "")
        password = os.getenv("MERCHRULES_PASSWORD", "")
        if not login or not password:
            sync_log.status = "skipped"
            sync_log.message = "MERCHRULES_LOGIN/PASSWORD не заданы"
            return

        clients = db.query(Client).filter(Client.merchrules_account_id.isnot(None)).all()
        synced = 0

        async with httpx.AsyncClient(timeout=30) as hx:
            token = await get_auth_token(hx, login, password)
            if not token:
                sync_log.status = "error"
                sync_log.message = "Ошибка авторизации Merchrules"
                return
            headers = {"Authorization": f"Bearer {token}"}

            for client in clients:
                try:
                    meetings_data = await fetch_site_meetings(hx, headers, client.merchrules_account_id)
                    if meetings_data.get("last_meeting"):
                        try:
                            client.last_meeting_date = datetime.fromisoformat(meetings_data["last_meeting"])
                            synced += 1
                        except Exception:
                            pass
                except Exception as exc:
                    logger.warning("MR meetings skip client %s: %s", client.id, exc)

        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = synced
        logger.info(f"✅ Synced meetings for {synced} clients")

    except Exception as e:
        logger.error(f"❌ Meetings sync error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()


# ============================================================================
# NOTIFICATION JOBS
# ============================================================================


async def job_check_overdue_checkups():
    """Ежедневно 08:00: создать задачи на просроченные чекапы"""
    db = SessionLocal()
    sync_log = SyncLog(
        integration="system",
        resource_type="checkups",
        action="process",
        status="in_progress",
    )
    
    try:
        logger.info("🔔 Checking overdue checkups...")
        
        # Найти просроченные чекапы
        overdue_checkups = db.query(CheckUp).filter(
            CheckUp.status == "overdue"
        ).all()
        
        created_tasks = 0
        for checkup in overdue_checkups:
            # Проверить есть ли уже задача для этого чекапа
            existing_task = db.query(Task).filter(
                Task.created_from_meeting_id == None,  # это грубо, можно улучшить
                Task.source == "checkup",
                Task.client_id == checkup.client_id,
            ).first()
            
            if not existing_task:
                # Создать задачу
                task = Task(
                    client_id=checkup.client_id,
                    title=f"Checkup: {checkup.type}",
                    description=f"Scheduled for {checkup.scheduled_date}",
                    status="plan",
                    priority="high",
                    source="checkup",
                )
                db.add(task)
                created_tasks += 1
                
                # Обновить флаг клиента
                client = db.query(Client).filter(Client.id == checkup.client_id).first()
                if client:
                    client.needs_checkup = True
        
        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = created_tasks
        logger.info(f"✅ Created {created_tasks} checkup tasks")
        
    except Exception as e:
        logger.error(f"❌ Checkup processing error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()


async def job_morning_plan():
    """Ежедневно 09:00 пн-пт: отправить утренний план"""
    db = SessionLocal()
    
    try:
        logger.info("📋 Sending morning plan...")
        
        # Получить просроченные чекапы
        overdue = db.query(CheckUp).filter(CheckUp.status == "overdue").all()
        
        # Получить задачи на сегодня
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        
        today_tasks = db.query(Task).filter(
            Task.due_date >= today_start,
            Task.due_date < today_end,
        ).all()
        
        logger.info(f"Morning: {len(overdue)} overdue checkups, {len(today_tasks)} tasks for today")
        
        # TODO: Отправить в Telegram
        # тут будет отправка сообщения в Telegram
        
    except Exception as e:
        logger.error(f"❌ Morning plan error: {e}")
    finally:
        db.close()


async def job_weekly_digest():
    """Каждую пятницу 17:00: еженедельный дайджест"""
    db = SessionLocal()
    
    try:
        logger.info("📊 Sending weekly digest...")
        
        # Статистика
        total_tasks = db.query(Task).count()
        completed_tasks = db.query(Task).filter(Task.status == "done").count()
        overdue_checkups = db.query(CheckUp).filter(CheckUp.status == "overdue").count()
        
        logger.info(f"Weekly: {completed_tasks}/{total_tasks} tasks done, {overdue_checkups} overdue")
        
        # TODO: Отправить в Telegram
        
    except Exception as e:
        logger.error(f"❌ Weekly digest error: {e}")
    finally:
        db.close()


# ============================================================================
# REGISTER JOBS
# ============================================================================


def start_scheduler():
    """Регистрировать все задачи"""
    
    # Hourly sync
    scheduler.add_job(
        job_sync_airtable_clients,
        "interval",
        hours=1,
        id="sync_airtable_clients",
        name="Sync Airtable Clients",
        replace_existing=True,
    )
    
    scheduler.add_job(
        job_sync_merchrules_analytics,
        "interval",
        hours=1,
        id="sync_merchrules_analytics",
        name="Sync Merchrules Analytics",
        replace_existing=True,
    )
    
    scheduler.add_job(
        job_sync_roadmap_tasks,
        "interval",
        hours=1,
        id="sync_roadmap_tasks",
        name="Sync Roadmap Tasks",
        replace_existing=True,
    )
    
    scheduler.add_job(
        job_sync_meetings,
        "interval",
        hours=1,
        id="sync_meetings",
        name="Sync Meetings",
        replace_existing=True,
    )
    
    # Daily jobs
    scheduler.add_job(
        job_check_overdue_checkups,
        "cron",
        hour=8,
        minute=0,
        id="check_overdue_checkups",
        name="Check Overdue Checkups",
        replace_existing=True,
    )
    
    scheduler.add_job(
        job_morning_plan,
        "cron",
        hour=9,
        minute=0,
        day_of_week="mon-fri",
        id="morning_plan",
        name="Morning Plan",
        replace_existing=True,
    )
    
    # Weekly digest (Friday 17:00)
    scheduler.add_job(
        job_weekly_digest,
        "cron",
        hour=17,
        minute=0,
        day_of_week="fri",
        id="weekly_digest",
        name="Weekly Digest",
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info("✅ Scheduler started with %d jobs", len(scheduler.get_jobs()))


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    start_scheduler()
    
    # Keep running
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        scheduler.shutdown()
