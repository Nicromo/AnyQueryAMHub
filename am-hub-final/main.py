import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Твои модули
from database import engine, get_db, Base, init_db
try:
    from auth import router as auth_router
    AUTH_AVAILABLE = True
except ImportError:
    AUTH_AVAILABLE = False

try:
    from tg_bot import router as tg_router
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False

try:
    from scheduler import start_scheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")

templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub OS")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄 DB Init...")
    init_db()
    if SCHEDULER_AVAILABLE:
        logger.info("⏰ Scheduler Start...")
        start_scheduler()
    logger.info("✅ AM Hub Ready!")
    yield

app.router.lifespan_context = lifespan

if AUTH_AVAILABLE:
    app.include_router(auth_router)
if TG_AVAILABLE:
    app.include_router(tg_router)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace", response_class=HTMLResponse)
async def workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/api/clients")
async def api_clients(db: Session = Depends(get_db)):
    try:
        from models import Client
        clients = db.query(Client).limit(20).all()
        return [{"id": c.id, "name": c.name, "segment": getattr(c, 'segment', 'Unknown')} for c in clients]
    except Exception as e:
        return {"error": str(e)}

@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
