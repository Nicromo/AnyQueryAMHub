import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from database import engine, get_db, Base, init_db, SessionLocal
from models import Client, Task, Meeting

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")

app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        print("✅ DB Initialized")
    except Exception as e:
        print(f"⚠️ DB Init Warning: {e}")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/api/clients")
async def api_clients():
    try:
        db = SessionLocal()
        clients = db.query(Client).limit(50).all()
        return [{"id": c.id, "name": c.name, "segment": c.segment, "health": c.health_score} for c in clients]
    except:
        return []
    finally:
        db.close()

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
