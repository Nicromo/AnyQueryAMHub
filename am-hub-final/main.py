import os
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from database import engine, get_db, Base, init_db
from contextlib import asynccontextmanager

# Шаблоны и статика
templates = Jinja2Templates(directory="templates")
app = FastAPI(title="AM Hub")

# Монтируем статику
app.mount("/static", StaticFiles(directory="static"), name="static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация БД при старте
    init_db()
    print("✅ Database initialized successfully")
    yield

app = FastAPI(lifespan=lifespan, title="AM Hub")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace", response_class=HTMLResponse)
async def get_workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "AM Hub is running"}

# Запуск только если файл запущен напрямую (для локальной отладки)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
