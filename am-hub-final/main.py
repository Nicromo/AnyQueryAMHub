import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from database import engine, Base, init_db, get_db
from models import Client, Task

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")

app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine:
        init_db()
        print("✅ DB Initialized")
    yield

app.router.lifespan_context = lifespan

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/api/clients")
async def api_clients(db: Session = Depends(get_db)):
    if not db: return []
    clients = db.query(Client).limit(50).all()
    return [{"id": c.id, "name": c.name, "segment": c.segment, "health": c.health_score} for c in clients]

@app.get("/health")
async def health():
    return {"status": "ok"}
