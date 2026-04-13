import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import engine, get_db, Base, init_db, SessionLocal
from models import Client, Task, Meeting, CheckUp
from sqlalchemy import text

# Integrations
from integrations import airtable, merchrules

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        print("✅ DB Connected")
    except Exception as e:
        print(f"⚠️ DB Error: {e}")
    yield


app = FastAPI(lifespan=lifespan)


# ============================================================================
# PAGES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================================
# API: CLIENTS
# ============================================================================


@app.get("/api/clients")
async def list_clients():
    """Получить список всех клиентов с аналитикой"""
    try:
        db = SessionLocal()
        clients = db.query(Client).limit(100).all()
        
        result = []
        for client in clients:
            result.append({
                "id": client.id,
                "name": client.name,
                "segment": client.segment,
                "manager_email": client.manager_email,
                "health_score": client.health_score,
                "revenue_trend": client.revenue_trend,
                "open_tickets": client.open_tickets,
                "last_meeting_date": client.last_meeting_date.isoformat() if client.last_meeting_date else None,
                "needs_checkup": client.needs_checkup,
            })
        
        return result
    except Exception as e:
        logger.error(f"Error listing clients: {e}")
        return []
    finally:
        db.close()


@app.get("/api/clients/{client_id}")
async def get_client_detail(client_id: int):
    """Получить детали клиента"""
    try:
        db = SessionLocal()
        client = db.query(Client).filter(Client.id == client_id).first()
        
        if not client:
            return {"error": "Client not found"}
        
        return {
            "id": client.id,
            "name": client.name,
            "segment": client.segment,
            "manager_email": client.manager_email,
            "health_score": client.health_score,
            "revenue_trend": client.revenue_trend,
            "activity_level": client.activity_level,
            "open_tickets": client.open_tickets,
            "last_meeting_date": client.last_meeting_date.isoformat() if client.last_meeting_date else None,
            "last_checkup": client.last_checkup.isoformat() if client.last_checkup else None,
            "needs_checkup": client.needs_checkup,
            "site_ids": client.site_ids,
            "airtable_record_id": client.airtable_record_id,
            "merchrules_account_id": client.merchrules_account_id,
            "last_sync_at": client.last_sync_at.isoformat() if client.last_sync_at else None,
        }
    except Exception as e:
        logger.error(f"Error getting client detail: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ============================================================================
# API: TASKS
# ============================================================================


@app.get("/api/clients/{client_id}/tasks")
async def get_client_tasks(client_id: int):
    """Получить задачи клиента"""
    try:
        db = SessionLocal()
        tasks = db.query(Task).filter(Task.client_id == client_id).all()
        
        result = []
        for task in tasks:
            result.append({
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "status": task.status,
                "priority": task.priority,
                "source": task.source,
                "created_at": task.created_at.isoformat(),
                "due_date": task.due_date.isoformat() if task.due_date else None,
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting client tasks: {e}")
        return []
    finally:
        db.close()


# ============================================================================
# API: MEETINGS
# ============================================================================


@app.get("/api/clients/{client_id}/meetings")
async def get_client_meetings(client_id: int):
    """Получить встречи клиента"""
    try:
        db = SessionLocal()
        meetings = db.query(Meeting).filter(Meeting.client_id == client_id).order_by(Meeting.date.desc()).all()
        
        result = []
        for meeting in meetings:
            result.append({
                "id": meeting.id,
                "date": meeting.date.isoformat(),
                "type": meeting.type,
                "source": meeting.source,
                "title": meeting.title,
                "summary": meeting.summary,
                "recording_url": meeting.recording_url,
                "mood": meeting.mood,
                "attendees": meeting.attendees,
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting client meetings: {e}")
        return []
    finally:
        db.close()


# ============================================================================
# API: CHECKUPS
# ============================================================================


@app.get("/api/clients/{client_id}/checkups")
async def get_client_checkups(client_id: int):
    """Получить чекапы клиента"""
    try:
        db = SessionLocal()
        checkups = db.query(CheckUp).filter(CheckUp.client_id == client_id).all()
        
        result = []
        for checkup in checkups:
            days_overdue = (datetime.utcnow() - checkup.scheduled_date).days if checkup.status == "overdue" else 0
            
            result.append({
                "id": checkup.id,
                "type": checkup.type,
                "status": checkup.status,
                "scheduled_date": checkup.scheduled_date.isoformat(),
                "completed_date": checkup.completed_date.isoformat() if checkup.completed_date else None,
                "priority": checkup.priority,
                "days_overdue": max(0, days_overdue),
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting client checkups: {e}")
        return []
    finally:
        db.close()


@app.get("/api/checkups/overdue")
async def get_overdue_checkups():
    """Получить все просроченные чекапы"""
    try:
        db = SessionLocal()
        checkups = db.query(CheckUp).filter(CheckUp.status == "overdue").all()
        
        result = []
        for checkup in checkups:
            client = db.query(Client).filter(Client.id == checkup.client_id).first()
            
            result.append({
                "id": checkup.id,
                "client_id": checkup.client_id,
                "client_name": client.name if client else "Unknown",
                "type": checkup.type,
                "scheduled_date": checkup.scheduled_date.isoformat(),
                "priority": checkup.priority,
                "days_overdue": (datetime.utcnow() - checkup.scheduled_date).days,
            })
        
        # Сортировать по priorityи дням просрочки
        result.sort(key=lambda x: (-x["priority"], -x["days_overdue"]))
        
        return result
    except Exception as e:
        logger.error(f"Error getting overdue checkups: {e}")
        return []
    finally:
        db.close()


# ============================================================================
# API: SYNC & MANAGEMENT
# ============================================================================


@app.post("/api/sync/clients")
async def sync_clients_from_airtable():
    """Синхронизировать клиентов из Airtable"""
    try:
        db = SessionLocal()
        airtable_clients = await airtable.get_clients(use_cache=False)
        logger.info(f"Syncing {len(airtable_clients)} clients from Airtable")
        
        synced = 0
        for at_client in airtable_clients:
            # Найти или создать клиента
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
        logger.info(f"✅ Synced {synced} clients")
        
        return {"status": "success", "synced": synced}
    except Exception as e:
        logger.error(f"Error syncing clients: {e}")
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.post("/api/sync/analytics")
async def sync_analytics():
    """Синхронизировать аналитику из Merchrules"""
    try:
        db = SessionLocal()
        clients = db.query(Client).all()
        
        updated = 0
        for client in clients:
            if not client.merchrules_account_id:
                continue
            
            # Получить аналитику
            analytics = await merchrules.fetch_account_analytics(client.merchrules_account_id)
            if analytics:
                client.health_score = analytics.get('health_score', 0)
                client.revenue_trend = analytics.get('revenue_trend')
                client.activity_level = analytics.get('activity_level', 'low')
                updated += 1
        
        client.last_sync_at = datetime.utcnow()
        db.commit()
        logger.info(f"✅ Updated analytics for {updated} clients")
        
        return {"status": "success", "updated": updated}
    except Exception as e:
        logger.error(f"Error syncing analytics: {e}")
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.get("/api/stats")
async def get_stats():
    """Получить статистику системы"""
    try:
        db = SessionLocal()
        
        total_clients = db.query(Client).count()
        total_tasks = db.query(Task).count()
        total_meetings = db.query(Meeting).count()
        overdue_checkups = db.query(CheckUp).filter(CheckUp.status == "overdue").count()
        
        avg_health_score = db.query(Client).filter(Client.health_score > 0).count()
        
        return {
            "total_clients": total_clients,
            "total_tasks": total_tasks,
            "total_meetings": total_meetings,
            "overdue_checkups": overdue_checkups,
            "clients_with_health_score": avg_health_score,
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return {}
    finally:
        db.close()
