from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import List, Optional
import os
from datetime import datetime

# Импорты твоих модулей (убедись, что они есть в папке)
try:
    from database import get_db, Client, Task, Meeting, UserRule
    from airtable_sync import sync_airtable_clients
    from merchrules_sync import MerchrulesSync
    DB_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Warning: Some modules failed to load: {e}")
    DB_AVAILABLE = False
    # Заглушки для теста интерфейса без БД
    class Client: pass
    class Task: pass

app = FastAPI(title="AM Hub OS")

# Подключение шаблонов и статики
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- ГЛАВНАЯ СТРАНИЦА (ИНТЕРФЕЙС) ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: Session = Depends(get_db) if DB_AVAILABLE else None):
    # Если БД доступна, грузим реальные данные
    clients_data = []
    tasks_data = []
    alerts_data = []
    
    if DB_AVAILABLE and db:
        try:
            # Берем топ-5 клиентов по здоровью (или последних)
            clients = db.query(Client).limit(5).all()
            for c in clients:
                clients_data.append({
                    "id": c.id,
                    "name": c.name if hasattr(c, 'name') else "Unknown",
                    "health": getattr(c, 'health_score', 80),
                    "revenue": "+12%", # Заглушка, если нет поля
                    "tickets": 2
                })
            
            # Берем активные задачи
            tasks = db.query(Task).filter(getattr(Task, 'status', None) != 'done').limit(5).all()
            for t in tasks:
                tasks_data.append({
                    "title": getattr(t, 'text', 'No title'),
                    "priority": "High" if getattr(t, 'priority', 'low') == 'high' else "Normal",
                    "due": "Today"
                })
                
            # Генерируем алерты (пример логики)
            if any(c.get('health', 100) < 70 for c in clients_data):
                alerts_data.append({"type": "critical", "msg": "Detected low Health Score in 2 accounts"})
        except Exception as e:
            print(f"DB Error: {e}")
            clients_data = [{"name": "Demo Client", "health": 65, "revenue": "-5%", "tickets": 5}]
            alerts_data = [{"type": "warning", "msg": "Database connection unstable"}]
    else:
        # Демо-данные если БД нет
        clients_data = [
            {"name": "12Storeez", "health": 62, "revenue": "+12%", "tickets": 3},
            {"name": "Lamoda", "health": 85, "revenue": "+5%", "tickets": 0},
            {"name": "M.Video", "health": 45, "revenue": "-10%", "tickets": 7}
        ]
        tasks_data = [
            {"title": "Fix Search Bug #402", "priority": "High", "due": "Today"},
            {"title": "Prepare QBR for 12Storeez", "priority": "Medium", "due": "Tomorrow"}
        ]
        alerts_data = [{"type": "critical", "msg": "ROAS dropped below 2.0 for 12Storeez"}]

    return templates.TemplateResponse("workspace.html", {
        "request": request, 
        "clients": clients_data, 
        "tasks": tasks_data,
        "alerts": alerts_data
    })

# --- API ДЛЯ ДАННЫХ (AJAX) ---
@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db) if DB_AVAILABLE else None):
    return {"status": "ok", "clients_count": len(clients_data) if DB_AVAILABLE else 3}

@app.post("/api/action")
async def perform_action(action: str):
    return {"status": "success", "message": f"Action {action} executed"}

# Запуск (для локального теста, Railway использует uvicorn через Procfile)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
