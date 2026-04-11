from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db, Base
from database import engine
import os

# Создание таблиц БД
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AM Hub")

# Подключение шаблонов и статики
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace")
async def get_workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/health")
async def health_check():
    return {"status": "ok", "db": "connected"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

