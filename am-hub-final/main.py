import os
import json
import httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from database import engine, get_db, Base, Client, Task, init_db
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")

app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Starting AM Hub...")
    init_db() # Создаем таблицы и мигрируем
    print("✅ Database ready.")
    
    # Авто-импорт из Airtable при первом запуске, если база пуста
    db = next(get_db())
    try:
        if db.query(Client).count() == 0:
            print("📥 No clients found. Fetching from Airtable...")
            await sync_airtable_initial(db)
            print(f"✅ Imported {db.query(Client).count()} clients.")
    except Exception as e:
        print(f"⚠️ Initial sync failed: {e}")
    finally:
        db.close()
        
    yield

app = FastAPI(lifespan=lifespan)

# --- AIRTABLE SYNC LOGIC ---
async def sync_airtable_initial(db):
    token = os.environ.get("AIRTABLE_TOKEN")
    base_id = os.environ.get("AIRTABLE_BASE_ID", "appEAS1rPKpevoIel")
    table_id = os.environ.get("AIRTABLE_TABLE_ID", "tblIKAi1gcFayRJTn")
    
    if not token:
        print("⚠️ AIRTABLE_TOKEN not set. Skipping sync.")
        return

    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.airtable.com/v0/{base_id}/{table_id}"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params({"pageSize": 100}))
            resp.raise_for_status()
            data = resp.json()
            
            for record in data.get("records", []):
                fields = record.get("fields", {})
                # Маппинг полей
                name = fields.get("Account") or fields.get("Название аккаунта")
                if not name: continue
                
                site_ids_raw = fields.get("Site ID") or fields.get("ID") or ""
                site_ids_str = str(site_ids_raw) if site_ids_raw else ""
                
                # Проверка на дубликат
                exists = db.query(Client).filter(Client.name == name).first()
                if not exists:
                    new_client = Client(
                        name=name,
                        domain=fields.get("Domain", ""),
                        segment=fields.get("Segment") or fields.get("сегмент", "SMB"),
                        manager_email=fields.get("CSM") or fields.get("Менеджер", ""),
                        site_ids=site_ids_str,
                        health_score=75.0
                    )
                    db.add(new_client)
            db.commit()
    except Exception as e:
        print(f"❌ Airtable sync error: {e}")

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/api/clients")
async def get_clients(db = Depends(get_db)):
    clients = db.query(Client).limit(50).all()
    return [{
        "id": c.id, 
        "name": c.name, 
        "segment": c.segment, 
        "health": c.health_score,
        "tickets": c.open_tickets,
        "trend": "up" if c.revenue_trend == "growth" else "down"
    } for c in clients]

@app.get("/api/stats")
async def get_stats(db = Depends(get_db)):
    total = db.query(Client).count()
    critical = db.query(Client).filter(Client.health_score < 40).count()
    tasks_count = db.query(Task).filter(Task.status == "todo").count()
    return {"total_clients": total, "critical_health": critical, "open_tasks": tasks_count}

@app.post("/api/tasks")
async def create_task(title: str, client_id: int = None, db = Depends(get_db)):
    task = Task(title=title, client_id=client_id)
    db.add(task)
    db.commit()
    return {"status": "ok", "id": task.id}

@app.get("/health")
def health():
    return {"status": "ok", "db": "connected"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
