import os
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from contextlib import asynccontextmanager

# Импортируем компоненты БД
try:
    from database import engine, get_db, Base, init_db, DATABASE_URL
except ImportError:
    engine = None
    DATABASE_URL = None
    def init_db(): pass
    def get_db(): yield None

templates = Jinja2Templates(directory="templates")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(lifespan=lifespan, title="AM Hub")

# Статика с отключением кэша для разработки (опционально)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace", response_class=HTMLResponse)
async def get_workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health_check():
    db_status = "connected" if engine else "disconnected"
    return {
        "status": "ok", 
        "message": "AM Hub is running",
        "database": db_status,
        "has_url": bool(DATABASE_URL)
    }

@app.get("/api/debug/env")
async def debug_env():
    return {
        "DATABASE_URL_present": bool(os.environ.get("DATABASE_URL")),
        "RAILWAY_ENVIRONMENT": os.environ.get("RAILWAY_ENVIRONMENT", "local")
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
