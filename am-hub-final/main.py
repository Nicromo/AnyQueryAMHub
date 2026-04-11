import os
import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from database import engine, get_db, Base, init_db, Client, Task, Meeting
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from sqlalchemy import desc

# --- Конфигурация ---
templates = Jinja2Templates(directory="templates")
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
AIRTABLE_TABLE_ID = os.environ.get("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine:
        init_db()
        # Синхронизация с Airtable при старте, если есть токен
        if AIRTABLE_TOKEN:
            await sync_airtable_startup()
        print("✅ Database initialized & Airtable synced")
    else:
        print("⚠️ No DATABASE_URL found. Running in demo mode.")
    yield

app = FastAPI(lifespan=lifespan, title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- Helper: Sync Airtable ---
async def sync_airtable_startup():
    if not AIRTABLE_TOKEN: return
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
        try:
            resp = await client.get(url, headers=headers, params={"maxRecords": 100}) # Лимит для старта
            resp.raise_for_status()
            data = resp.json().get("records", [])
            
            db = next(get_db())
            for record in data:
                fields = record.get("fields", {})
                name = fields.get("Account") or fields.get("Название аккаунта", "Unknown")
                # Простая логика: если нет такого клиента, создаем
                if not db.query(Client).filter(Client.name == name).first():
                    client_obj = Client(
                        name=name,
                        segment=fields.get("Segment", "SMB"),
                        health_score=75.0, # Дефолт
                        last_checkup=datetime.now() - timedelta(days=10)
                    )
                    db.add(client_obj)
            db.commit()
            print(f"✅ Imported {len(data)} clients from Airtable")
        except Exception as e:
            print(f"❌ Airtable sync error: {e}")

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health():
    return {"status": "ok", "db": "connected" if engine else "disconnected"}

@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    if not engine: return {"clients": 0, "tasks": 0, "meetings": 0}
    return {
        "clients": db.query(Client).count(),
        "tasks": db.query(Task).count(),
        "meetings": db.query(Meeting).count()
    }

@app.get("/api/clients")
async def get_clients(db: Session = Depends(get_db)):
    if not engine: return []
    clients = db.query(Client).order_by(desc(Client.health_score)).limit(20).all()
    return [
        {
            "id": c.id, "name": c.name, "segment": c.segment, 
            "health": c.health_score, "last_checkup": c.last_checkup.isoformat() if c.last_checkup else None
        } for c in clients
    ]

@app.get("/api/tasks")
async def get_tasks(db: Session = Depends(get_db)):
    if not engine: return []
    tasks = db.query(Task).filter(Task.status != "done").order_by(desc(Task.priority)).limit(10).all()
    return [
        {"id": t.id, "title": t.title, "priority": t.priority, "status": t.status} for t in tasks
    ]

@app.post("/api/tasks")
async def create_task(task: dict, db: Session = Depends(get_db)):
    if not engine: return {"error": "No DB"}
    new_task = Task(**task)
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return {"id": new_task.id, "status": "created"}

@app.get("/api/client/{client_id}")
async def get_client_details(client_id: int, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client: raise HTTPException(status_code=404, detail="Client not found")
    return {
        "id": client.id, "name": client.name, "domain": client.domain, 
        "health": client.health_score, "segment": client.segment
    }
