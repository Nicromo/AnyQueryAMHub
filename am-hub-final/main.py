import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from database import engine, get_db, Base, init_db
from models import Client, Task, Meeting

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")
app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health():
    return {"status": "ok", "db": "connected"}

@app.get("/api/clients")
async def api_clients(db: Session = Depends(get_db)):
    clients = db.query(Client).limit(50).all()
    return [{"id": c.id, "name": c.name, "segment": c.segment, "health": c.health_score} for c in clients]

@app.post("/api/tasks")
async def api_create_task(task: dict, db: Session = Depends(get_db)):
    new_task = Task(title=task.get("title", "New Task"), status="open")
    db.add(new_task)
    db.commit()
    return {"status": "success", "id": new_task.id}
