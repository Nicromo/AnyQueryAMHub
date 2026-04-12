import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import engine, get_db, Base, init_db, SessionLocal
from models import Client
from sqlalchemy import text

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
        with SessionLocal() as db: db.execute(text("SELECT 1"))
        print("✅ DB Connected")
    except Exception as e: print(f"⚠️ DB Error: {e}")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health(): return {"status": "ok"}

@app.get("/api/clients")
async def get_clients():
    try:
        db = SessionLocal()
        clients = db.query(Client).limit(50).all()
        return [{"id": c.id, "name": c.name, "segment": c.segment} for c in clients]
    except: return []
    finally: db.close()
