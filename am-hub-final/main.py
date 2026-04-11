import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from database import engine, get_db, Base, init_db, SessionLocal
from models import Client, Task, Meeting  # Импорт моделей

# Шаблоны и статика
templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")

app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # Пробуем инициализировать БД
        init_db()
        print("✅ Database initialized successfully")
        
        # Проверяем подключение
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            print("✅ Database connection verified")
            
            # Авто-импорт клиентов, если таблица пуста
            if db.query(Client).count() == 0:
                print("⚠️ Clients table is empty. Triggering Airtable sync...")
                # Здесь можно вызвать функцию синхронизации, если она есть
                # from airtable_sync import sync_clients
                # await sync_clients(db)
    except Exception as e:
        print(f"⚠️ Database startup warning (non-critical): {e}")
        # Не прерываем запуск приложения, если БД недоступна сразу
    yield

app = FastAPI(lifespan=lifespan, title="AM Hub")

# --- API ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace", response_class=HTMLResponse)
async def get_workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "AM Hub is running"}

@app.get("/api/clients")
async def get_clients():
    try:
        db = SessionLocal()
        clients = db.query(Client).limit(50).all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "domain": c.domain,
                "segment": c.segment,
                "health_score": c.health_score or 0,
                "last_checkup": str(c.last_checkup) if c.last_checkup else None
            }
            for c in clients
        ]
    except Exception as e:
        return [] # Возвращаем пустой список при ошибке БД
    finally:
        db.close()

@app.post("/api/tasks")
async def create_task(task_data: dict):
    # Заглушка для создания задачи
    return {"status": "success", "message": f"Task created: {task_data.get('title')}"}

@app.get("/api/stats")
async def get_stats():
    try:
        db = SessionLocal()
        total_clients = db.query(Client).count()
        active_tasks = db.query(Task).filter(Task.status == "open").count() if 'Task' in globals() else 0
        return {
            "total_clients": total_clients,
            "active_tasks": active_tasks,
            "revenue_trend": "+12%" # Заглушка
        }
    except:
        return {"total_clients": 0, "active_tasks": 0, "revenue_trend": "N/A"}
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
