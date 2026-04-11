from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import os
import uvicorn

app = FastAPI(title="AM Hub")

# Подключаем статику и шаблоны
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- СУЩЕСТВУЮЩИЕ ЗАГЛУШКИ (чтобы не ломать другие роуты) ---
@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/workspace")
async def workspace(request: Request):
    return templates.TemplateResponse("workspace.html", {"request": request})

@app.get("/api/stats")
async def get_stats():
    return {"status": "ok", "message": "UI Test Success"}

# --- ЗАПУСК ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
