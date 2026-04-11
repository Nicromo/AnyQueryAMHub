import os
import random
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from database import engine, get_db, init_db
from models import Client, Task, Meeting
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

# --- Имитация внешних сервисов для демо (если нет ключей) ---
def fetch_airtable_clients():
    # Здесь был бы реальный запрос к Airtable API
    # Возвращаем фейковые данные для демонстрации, если база пуста
    return [
        {"name": "12Storeez", "domain": "12storeez.com", "segment": "ENT", "health": 62, "trend": "drop", "tickets": 3},
        {"name": "Lamoda", "domain": "lamoda.ru", "segment": "ENT", "health": 85, "trend": "growth", "tickets": 0},
        {"name": "Tinkoff", "domain": "tinkoff.ru", "segment": "ENT", "health": 92, "trend": "stable", "tickets": 1},
        {"name": "MVideo", "domain": "mvideo.ru", "segment": "SME", "health": 45, "trend": "drop", "tickets": 5},
        {"name": "Detmir", "domain": "detmir.ru", "segment": "SME", "health": 78, "trend": "growth", "tickets": 2},
    ]

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = next(get_db())
    # Заполняем базу клиентами, если она пуста
    if db.query(Client).count() == 0:
        print("🌱 Seeding database with demo clients...")
        data = fetch_airtable_clients()
        for item in data:
            client = Client(
                name=item["name"], 
                domain=item["domain"], 
                segment=item["segment"],
                health_score=item["health"],
                revenue_trend=item["trend"],
                open_tickets=item["tickets"],
                last_checkup=datetime.now() - timedelta(days=random.randint(1, 60))
            )
            db.add(client)
            
            # Добавим пару задач
            db.add(Task(client_id=client.id, title=f"Проверить падение ROAS у {client.name}", priority="high", status="todo"))
            if item["segment"] == "ENT":
                db.add(Task(client_id=client.id, title=f"Подготовить QBR для {client.name}", priority="medium", status="in_progress"))
                
        db.commit()
        print("✅ Database seeded successfully!")
    yield

app = FastAPI(lifespan=lifespan, title="AM Hub")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace", response_class=HTMLResponse)
async def workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

# --- API ENDPOINTS ---

@app.get("/api/clients")
def get_clients(db: Session = Depends(get_db)):
    clients = db.query(Client).all()
    return [
        {
            "id": c.id, "name": c.name, "domain": c.domain, "segment": c.segment,
            "health_score": c.health_score, "revenue_trend": c.revenue_trend,
            "open_tickets": c.open_tickets, "last_checkup": c.last_checkup.isoformat() if c.last_checkup else None
        } for c in clients
    ]

@app.get("/api/tasks")
def get_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).filter(Task.status != "done").limit(10).all()
    return [
        {"id": t.id, "title": t.title, "priority": t.priority, "status": t.status} for t in tasks
    ]

@app.post("/api/tasks")
def create_task(title: str, priority: str = "medium", db: Session = Depends(get_db)):
    new_task = Task(title=title, priority=priority, status="todo")
    db.add(new_task)
    db.commit()
    return {"status": "ok", "id": new_task.id}

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    total_clients = db.query(Client).count()
    critical_health = db.query(Client).filter(Client.health_score < 50).count()
    total_tasks = db.query(Task).filter(Task.status != "done").count()
    return {"total_clients": total_clients, "critical_health": critical_health, "total_tasks": total_tasks}

@app.get("/health")
def health_check():
    return {"status": "ok", "db": "connected"}
