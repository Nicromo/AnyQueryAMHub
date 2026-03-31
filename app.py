#!/usr/bin/env python3
"""
Веб-интерфейс для массового создания задач в дорожной карте.
Креды: ~/.search-checkup-creds.json → merchrules.
Запуск: python3 app.py  (порт 5051)
"""
import io
import json
import os
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for

# путь к папке приложения для импорта task_defaults и creds
APP_DIR = Path(__file__).resolve().parent
import sys
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from task_defaults import apply_task_defaults
from creds import load_merchrules_creds, save_merchrules_creds, save_grok_api_key, COPY_API_BASE_URL

try:
    from ollama_meeting import (
        meeting_text_to_tasks,
        grok_available,
        parse_transcription_metadata,
        process_transcription,
        cancel_generation as _cancel_ollama_generation,
        get_model_name,
        MEETING_TO_TASKS_PROMPT,
        TRANSCRIPTION_PROMPT_TEMPLATE,
    )
    ollama_available = grok_available  # backward compat alias
    get_best_available_model = get_model_name  # backward compat alias
except ImportError:
    meeting_text_to_tasks = None
    grok_available = None
    ollama_available = None
    parse_transcription_metadata = None
    process_transcription = None
    _cancel_ollama_generation = None
    get_model_name = None
    get_best_available_model = None
    MEETING_TO_TASKS_PROMPT = ""
    TRANSCRIPTION_PROMPT_TEMPLATE = ""

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production-please")

# URL merchrules: env var → production по умолчанию
PROD_MERCHRULES_BASE_URL = os.environ.get("MERCHRULES_BASE_URL", "https://merchrules.any-platform.ru").rstrip("/")


def _get_creds():
    """Получить кредсы: session → env vars → файл."""
    if session.get("mr_login"):
        return (
            session.get("mr_base_url", PROD_MERCHRULES_BASE_URL),
            session["mr_login"],
            session.get("mr_password", ""),
        )
    env_login = os.environ.get("MERCHRULES_LOGIN", "").strip()
    env_pw = os.environ.get("MERCHRULES_PASSWORD", "").strip()
    if env_login and env_pw:
        return PROD_MERCHRULES_BASE_URL, env_login, env_pw
    return load_merchrules_creds()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        base_url, login, password = _get_creds()
        if not (login and password):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📋</text></svg>">
<title>Войти — Roadmap Tasks</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0d0d0d;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e8e8e8}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:16px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 8px 40px rgba(0,0,0,.5)}
h1{font-size:1.35rem;font-weight:700;margin-bottom:6px;color:#fff}
.sub{font-size:.85rem;color:#888;margin-bottom:28px}
label{display:block;font-size:.8rem;color:#aaa;margin-bottom:5px;font-weight:500;letter-spacing:.02em}
input{width:100%;padding:10px 14px;border:1px solid #333;border-radius:8px;background:#111;color:#e8e8e8;font-size:.95rem;margin-bottom:16px;outline:none;transition:border-color .15s}
input:focus{border-color:#6c8ef0}
.btn{width:100%;padding:11px;background:#6c8ef0;color:#fff;border:none;border-radius:8px;font-size:.95rem;font-weight:600;cursor:pointer;transition:background .15s}
.btn:hover{background:#5a7ae0}
.err{background:rgba(248,81,73,.15);border:1px solid rgba(248,81,73,.4);border-radius:8px;padding:10px 14px;font-size:.85rem;color:#f85149;margin-bottom:16px}
.logo{font-size:1.8rem;margin-bottom:16px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">📋</div>
  <h1>Roadmap Tasks</h1>
  <p class="sub">Войдите через аккаунт Merchrules</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <label>Логин</label>
    <input type="text" name="login" placeholder="username" required autofocus autocomplete="username">
    <label>Пароль</label>
    <input type="password" name="password" placeholder="••••••••" required autocomplete="current-password">
    <button type="submit" class="btn">Войти</button>
  </form>
</div>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("mr_login"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        login_input = (request.form.get("login") or "").strip()
        pw_input = request.form.get("password") or ""
        base_url = PROD_MERCHRULES_BASE_URL
        import requests as _req
        try:
            r = _req.post(
                f"{base_url}/backend-v2/auth/login",
                json={"username": login_input, "password": pw_input},
                timeout=10,
            )
            if r.status_code == 200:
                session.permanent = True
                session["mr_login"] = login_input
                session["mr_password"] = pw_input
                session["mr_base_url"] = base_url
                return redirect(url_for("index"))
            else:
                error = "Неверный логин или пароль"
        except Exception as e:
            error = f"Ошибка подключения к Merchrules: {e}"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/robots.txt")
def robots_txt():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}


@app.after_request
def add_noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# Справочники для селектов (коды из доки API)
STATUS_CHOICES = ["plan", "in_progress", "done", "blocker", "pending"]
PRIORITY_CHOICES = ["low", "medium", "high"]
ASSIGNEE_CHOICES = [("any", "Диджинетика"), ("partner", "Партнёр")]
TEAM_CHOICES = ["CS", "Int", "UXUI", "DEV", "ANALYTICS", "PRODUCT", "IMPROVE", "ANYRECS", "ANYREVIEWS", "LINGUISTS", "FRONTEND", "BACKEND", "DATASCI", "TRACKING"]
TASK_TYPE_CHOICES = ["tracking", "search_quality", "analytics", "data_science", "rnd", "integration", "marketing", "merchandising", "research"]
PRODUCT_CHOICES = ["any_query_web", "any_query_app", "any_recs_web", "any_recs_app", "any_reviews", "any_images"]


def csv_cell(s):
    if s is None or s == "":
        return ""
    s = str(s).strip()
    if "," in s or '"' in s or "\n" in s or "\r" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def build_task_csv_row(fields):
    """Одна строка CSV из полей задачи (после apply_task_defaults). Формат как в import_one_task.py."""
    header = "title,description,status,priority,team,task_type,assignee,product,link,due_date"
    f = apply_task_defaults(fields)
    row = ",".join([
        csv_cell(f.get("title")),
        csv_cell(f.get("description")),
        csv_cell(f.get("status")),
        csv_cell(f.get("priority")),
        csv_cell(f.get("team")),
        csv_cell(f.get("task_type")),
        csv_cell(f.get("assignee")),
        csv_cell(f.get("product")),
        csv_cell(f.get("link")),
        csv_cell(f.get("due_date")),
    ])
    return header + "\n" + row


def build_task_csv_multi_site(fields, site_ids):
    """CSV с колонкой site_id: одна строка на партнёра, одна задача в одном запросе."""
    header = "site_id,title,description,status,priority,team,task_type,assignee,product,link,due_date"
    f = apply_task_defaults(fields)
    cells = [
        csv_cell(f.get("title")),
        csv_cell(f.get("description")),
        csv_cell(f.get("status")),
        csv_cell(f.get("priority")),
        csv_cell(f.get("team")),
        csv_cell(f.get("task_type")),
        csv_cell(f.get("assignee")),
        csv_cell(f.get("product")),
        csv_cell(f.get("link")),
        csv_cell(f.get("due_date")),
    ]
    rows = [",".join([csv_cell(sid), *cells]) for sid in site_ids]
    return header + "\n" + "\n".join(rows)


def _csv_to_bytes(csv_body):
    """CSV в байты UTF-8 (как в import_one_task.py — без BOM)."""
    return csv_body.encode("utf-8")


def parse_site_ids(text, file_content=None):
    """Из текста (запятая/переносы) и опционально из файла собрать список site_id."""
    ids = set()
    if text:
        for part in re.split(r"[\s,;\n]+", text):
            part = part.strip()
            if part:
                ids.add(part)
    if file_content:
        if isinstance(file_content, bytes):
            file_content = file_content.decode("utf-8", errors="replace")
        for line in file_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # первый столбец или вся строка
            first = line.split(",")[0].strip() or line.strip()
            if first:
                ids.add(first)
    return sorted(ids, key=lambda x: (int(x) if x.isdigit() else 0, x))


INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Roadmap — создание задач</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📋</text></svg>">
  <style>
    :root {
      --bg: #1a1a1e;
      --text: #e4e4e7;
      --muted: #a1a1aa;
      --card: rgba(30, 30, 35, 0.9);
      --card-border: rgba(255,255,255,0.08);
      --accent: #e84c9a;
      --accent-dim: rgba(232, 76, 154, 0.4);
      --glass: rgba(255, 255, 255, 0.04);
    }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; min-height: 100vh; background: var(--bg); color: var(--text); line-height: 1.5; }
    .page { padding: 24px; max-width: 900px; margin: 0 auto; }
    .header { margin-bottom: 24px; }
    .header h1 { font-size: 1.4rem; margin: 0; font-weight: 600; }
    .sub { color: var(--muted); font-size: 0.9rem; margin: 8px 0 0 0; }
    .card { background: var(--card); border: 1px solid var(--card-border); border-radius: 16px; padding: 20px; margin-bottom: 20px; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 6px; }
    input[type="text"], input[type="number"], select, textarea {
      width: 100%; padding: 10px 14px; border: 1px solid var(--card-border); border-radius: 12px;
      background: var(--glass); color: var(--text); font-size: 14px; box-sizing: border-box;
    }
    input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent-dim); }
    textarea { min-height: 80px; resize: vertical; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
    @media (max-width: 700px) { .grid2, .grid3 { grid-template-columns: 1fr; } }
    .btn { padding: 12px 24px; border-radius: 12px; border: none; font-size: 14px; font-weight: 500; cursor: pointer; }
    .btn-primary { background: var(--accent); color: #fff; cursor: pointer; position: relative; z-index: 1; }
    .btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
    .btn-secondary { background: var(--glass); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover:not(:disabled) { background: rgba(255,255,255,0.08); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .help { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
    #msg { margin-top: 16px; padding: 14px; border-radius: 12px; white-space: pre-wrap; max-height: 40vh; overflow-y: auto; }
    #msg.ok { background: rgba(46, 160, 67, 0.2); border: 1px solid rgba(46, 160, 67, 0.5); }
    #msg.err { background: rgba(248, 81, 73, 0.15); border: 1px solid rgba(248, 81, 73, 0.4); }
    .drop-zone { border: 2px dashed var(--card-border); border-radius: 12px; padding: 16px; text-align: center; background: var(--glass); margin-top: 8px; position: relative; cursor: pointer; transition: border-color .15s, background .15s; }
    .drop-zone:hover, .drop-zone.drag-over { border-color: var(--accent-dim); background: rgba(255, 107, 107, 0.06); }
    .drop-zone.has-files { border-color: var(--accent-dim); }
    .drop-zone .drop-zone-input { position: absolute; left: -9999px; width: 1px; height: 1px; opacity: 0; pointer-events: none; overflow: hidden; }
    .form-group { margin-bottom: 16px; }
    .form-group:last-of-type { margin-bottom: 0; }
    .form-group label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 6px; }
    .form-group input { width: 100%; padding: 10px 14px; border: 1px solid var(--card-border); border-radius: 12px; background: var(--glass); color: var(--text); font-size: 14px; box-sizing: border-box; }
    .form-group input:focus { outline: none; border-color: var(--accent-dim); }
    .load-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); z-index: 9999; align-items: center; justify-content: center; flex-direction: column; gap: 16px; pointer-events: none; }
    .load-overlay.visible { display: flex; pointer-events: auto; }
    .load-overlay .spinner { width: 48px; height: 48px; border: 4px solid var(--card-border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
    .load-overlay .load-text { color: var(--text); font-size: 16px; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .gear-btn {
      width: 40px; height: 40px; border-radius: 12px;
      background: var(--card); border: 1px solid var(--card-border);
      backdrop-filter: blur(12px); cursor: pointer; display: flex; align-items: center; justify-content: center;
      color: var(--muted); font-size: 18px; transition: color .2s, background .2s; flex-shrink: 0;
    }
    .gear-btn:hover { color: var(--accent); background: var(--glass); }
    .mode-switch { display: flex; gap: 0; background: var(--glass); border: 1px solid var(--card-border); border-radius: 12px; padding: 4px; margin-bottom: 20px; position: relative; z-index: 10; }
    .mode-switch .mode-tab { flex: 1; text-align: center; padding: 10px 16px; border-radius: 10px; cursor: pointer; color: var(--muted); font-size: 14px; transition: background .2s, color .2s; border: none; background: transparent; font-family: inherit; }
    .mode-switch .mode-tab:hover { color: var(--text); }
    .mode-switch .mode-tab.active { background: var(--card); color: var(--text); font-weight: 500; }
  </style>
</head>
<body>
  <div class="load-overlay" id="loadOverlay">
    <div class="spinner"></div>
    <div class="load-text" id="loadText">Создание задач…</div>
  </div>
  <div class="page">
    <div class="header" style="display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap;">
      <div style="min-width:0;">
        <h1 style="margin:0;">Roadmap — создание задач</h1>
        <p class="sub" style="margin-top:6px;">Одна и та же задача для каждого указанного site_id. Статус/приоритет по умолчанию: plan, medium.</p>
      </div>
      <div style="display:flex;gap:8px;flex-shrink:0;">
        <button type="button" class="btn btn-secondary" id="promptSettingsBtn" title="Настройки: промпты и Groq API key" style="display:inline-flex; align-items:center; gap:8px;" onclick="var m=document.getElementById('promptModal');if(m)m.style.display='flex';">&#9881; Настройки{% if not groq_key_set %} <span style="background:#e74c3c;color:#fff;border-radius:50%;width:16px;height:16px;font-size:10px;display:inline-flex;align-items:center;justify-content:center;font-weight:700;">!</span>{% endif %}</button>
        <a href="/logout" class="btn btn-secondary" style="display:inline-flex;align-items:center;gap:6px;text-decoration:none;" title="Выйти">&#x2192; Выйти</a>
      </div>
    </div>
    {% if creds_ok %}
    <div class="mode-switch">
      <button type="button" class="mode-tab active" data-mode="meeting" onclick="applyMode('meeting')">Итоги встречи</button>
      <button type="button" class="mode-tab" data-mode="bulk" onclick="applyMode('bulk')">Массовое добавление</button>
    </div>
    <div id="modeMeeting">
    <div class="card" id="kvenCard">
      <label>Итоги встречи с партнёром</label>
      <p class="help" style="margin-bottom:8px;">Одно поле: вставьте текст или перетащите сюда .txt транскрипт из Толка (звонок с партнёром).</p>
      <textarea id="meetingSummary" placeholder="Вставьте текст итогов или перетащите .txt сюда…" rows="6" style="width:100%; min-height:100px; padding:10px 14px; border:2px dashed var(--card-border); border-radius:12px; background:var(--glass); color:var(--text); resize:vertical; font:inherit; transition: border-color .15s, background .15s;"></textarea>
      <input type="file" id="kvenTranscriptionFile" accept=".txt" style="display:none;">
      <div style="display:flex; align-items:center; gap:10px; margin-top:6px;">
        <button type="button" class="btn btn-secondary" id="kvenChooseFileBtn" style="padding:6px 14px; font-size:0.85rem;">Выбрать .txt файл</button>
        <span class="help" id="kvenFileName" style="color:var(--muted);"></span>
      </div>
      <div id="transcriptionMetaBlock" style="display:none; margin-top:16px;">
        <label>Сотрудники Any (наша сторона). Отметьте галочками</label>
        <p class="help" id="transcriptionTitleHint"></p>
        <div id="transcriptionSpeakersCheckboxes" style="display:flex; flex-wrap:wrap; gap:10px; margin-top:8px;"></div>
        <div style="display:flex; align-items:center; gap:10px; margin-top:12px; flex-wrap:wrap;">
          <button type="button" class="btn btn-primary" id="transcriptionProcessBtn" style="display:none;">Обработать транскрипцию</button>
          <button type="button" class="btn btn-secondary" id="transcriptionCancelBtn" style="display:none; padding:8px 16px; font-size:0.85rem;">✕ Отменить</button>
          <span class="help" id="transcriptionStatus" style="color:var(--muted);"></span>
        </div>
      </div>
      <div id="transcriptionResultBlock" style="display:none; margin-top:16px;">
        <label>Саммари</label>
        <div id="transcriptionSummary" style="padding:10px; background:var(--glass); border-radius:12px; margin-bottom:12px; white-space:pre-wrap;"></div>
        <label>Постмит-сообщение (для мессенджера)</label>
        <textarea id="transcriptionPostMessage" rows="4" style="width:100%; margin-top:6px;"></textarea>
        <button type="button" class="btn btn-secondary" id="transcriptionCopyMsgBtn" style="margin-top:6px;">Скопировать сообщение</button>
        <label style="margin-top:16px;">Задачи из транскрипции</label>
        <div id="transcriptionTasksList"></div>
        <label style="margin-top:16px;">Партнёры (site_id)</label>
        <textarea id="transcriptionSiteIds" rows="2" placeholder="2262, 305"></textarea>
        <button type="button" class="btn btn-primary" id="transcriptionSendTasksBtn" style="margin-top:8px;">Отправить задачи в дашборд</button>
        <div id="transcriptionSendResult" style="margin-top:8px; padding:12px; border-radius:12px; display:none; white-space:pre-wrap;"></div>
      </div>
      <label style="margin-top:16px;">Партнёры (site_id)</label>
      <p class="help">Укажите site_id партнёра. Через запятую или с новой строки — один или несколько</p>
      <textarea id="kvenSiteIdsInput" rows="2" placeholder="221&#10;2262, 305" style="margin-top:6px;"></textarea>
      <div style="margin-top:16px; text-align:center;">
        <button type="button" class="btn btn-primary" id="kvenGenerateBtn" style="min-width:240px; font-size:1rem; padding:10px 28px;">Сформировать задачи</button>
        <button type="button" class="btn btn-secondary" id="kvenCancelBtn" style="display:none; margin-left:10px; padding:10px 16px; font-size:0.9rem;">✕ Отменить</button>
      </div>
      <div style="margin-top:8px; text-align:center;">
        <span class="help" id="kvenStatus" style="display:inline-block; min-width:180px;"></span>
        <span id="kvenModelLabel" style="font-size:0.75rem; color:var(--muted); margin-left:8px;"></span>
      </div>
    </div>
    <div class="card" id="kvenPreviewCard" style="display:none;">
      <label>Предпросмотр задач (отредактируйте при необходимости, отметьте галочками и отправьте)</label>
      <div id="kvenTasksList"></div>
      <label style="margin-top:16px;">Партнёры (site_id)</label>
      <p class="help">Через запятую или с новой строки — один или несколько</p>
      <textarea id="kvenSiteIds" rows="3" placeholder="2262&#10;305, 1487"></textarea>
      <div style="margin-top:12px;">
        <button type="button" class="btn btn-primary" id="kvenSendBtn">Отправить выбранные задачи в дашборд</button>
        <span class="help" id="kvenSendStatus" style="margin-left:12px;"></span>
      </div>
      <div id="kvenSendResult" style="margin-top:12px; padding:12px; border-radius:12px; display:none; white-space:pre-wrap; max-height:30vh; overflow-y:auto;"></div>
    </div>
    </div>
    <div id="modeBulk" style="display:none;">
    <h2 style="font-size:1.1rem; margin:0 0 16px;">Массовое добавление</h2>
    <form method="post" action="/" id="f" enctype="multipart/form-data">
      <div class="card">
        <label>Название задачи (обязательно) *</label>
        <input type="text" name="title" value="{{ request.form.get('title', '') }}" placeholder="Протестировать вектора" required>
      </div>
      <div class="card">
        <label>Описание</label>
        <textarea name="description" placeholder="Тестовая задача">{{ request.form.get('description', '') }}</textarea>
      </div>
      <div class="card">
        <label>Статус</label>
        <select name="status">
          {% for v in status_choices %}
          <option value="{{ v }}" {{ 'selected' if request.form.get('status', 'plan') == v else '' }}>{{ v }}</option>
          {% endfor %}
        </select>
        <p class="help">Если не указан — подставится plan</p>
      </div>
      <div class="card">
        <label>Приоритет</label>
        <select name="priority">
          {% for v in priority_choices %}
          <option value="{{ v }}" {{ 'selected' if request.form.get('priority', 'medium') == v else '' }}>{{ v }}</option>
          {% endfor %}
        </select>
        <p class="help">Если не указан — medium</p>
      </div>
      <div class="card">
        <label>Исполнитель</label>
        <select name="assignee">
          {% for code, label in assignee_choices %}
          <option value="{{ code }}" {{ 'selected' if request.form.get('assignee', 'any') == code else '' }}>{{ label }} ({{ code }})</option>
          {% endfor %}
        </select>
        <p class="help">Если не указан — Диджинетика (any)</p>
      </div>
      <div class="card">
        <div class="grid2">
          <div>
            <label>Команда</label>
            <select name="team">
              <option value="">—</option>
              {% for v in team_choices %}
              <option value="{{ v }}" {{ 'selected' if request.form.get('team') == v else '' }}>{{ v }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label>Тип задачи</label>
            <select name="task_type">
              <option value="">—</option>
              {% for v in task_type_choices %}
              <option value="{{ v }}" {{ 'selected' if request.form.get('task_type') == v else '' }}>{{ v }}</option>
              {% endfor %}
            </select>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="grid2">
          <div>
            <label>Продукт</label>
            <select name="product">
              <option value="">—</option>
              {% for v in product_choices %}
              <option value="{{ v }}" {{ 'selected' if request.form.get('product') == v else '' }}>{{ v }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label>Срок (YYYY-MM-DD)</label>
            <input type="text" name="due_date" value="{{ request.form.get('due_date', '') }}" placeholder="2025-03-01">
          </div>
        </div>
        <label style="margin-top:12px;">Ссылка (Jira и т.д.)</label>
        <input type="text" name="link" value="{{ request.form.get('link', '') }}" placeholder="https://jira...">
      </div>
      <div class="card">
        <label>Партнёры (site_id)</label>
        <p class="help">Через запятую, в столбик (файл не загружается — вставьте список в поле ниже)</p>
        <textarea name="site_ids" rows="4" placeholder="2262&#10;305, 1487">{{ request.form.get('site_ids', '') }}</textarea>
      </div>
      <button type="submit" class="btn btn-primary" id="submitBtn">Создать задачи</button>
    </form>
    </div>
    {% if msg %}
    <div id="msg" class="{{ msg_class }}">{{ msg }}</div>
    {% endif %}
    {% else %}
    <div class="card">
      <h3 style="margin:0 0 8px;">Вход в Roadmap API</h3>
      <p class="sub" style="margin-bottom:20px;">Логин и пароль сохраняются локально в <code>~/.search-checkup-creds.json</code> — потом не нужно вводить снова.</p>
      <form id="credsForm">
        <div class="form-group">
          <label>Логин</label>
          <input type="text" id="credsLogin" name="login" placeholder="username" value="{{ creds_login or '' }}" required autocomplete="username">
        </div>
        <div class="form-group">
          <label>Пароль</label>
          <input type="password" id="credsPassword" name="password" placeholder="Пароль" required autocomplete="current-password">
        </div>
        <button type="submit" class="btn btn-primary" id="credsSubmitBtn" style="margin-top:8px;">Сохранить и войти</button>
      </form>
      <p id="credsMsg" class="help" style="margin-top:12px; display:none;"></p>
    </div>
    {% endif %}
  </div>
  <div class="modal-overlay" id="promptModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:99999; align-items:center; justify-content:center; padding:24px; pointer-events:auto;">
    <div class="card" style="max-width:700px; max-height:90vh; overflow-y:auto; position:relative; z-index:100000;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
        <h3 style="margin:0;">Настройки</h3>
        <button type="button" class="btn btn-secondary" id="promptModalClose" title="Закрыть" style="cursor:pointer; min-width:40px;" onclick="var m=document.getElementById('promptModal');if(m)m.style.display='none';">×</button>
      </div>

      <h4 style="margin:0 0 10px; color:var(--accent);">🤖 Groq API</h4>
      <p class="help" style="margin-bottom:10px;">Ключ для генерации задач и саммари через <b>llama-3.3-70b-versatile</b>. Получить бесплатно на <a href="https://console.groq.com" target="_blank" style="color:var(--accent);">console.groq.com</a>.</p>
      <form id="groqKeyForm" onsubmit="return false;" style="margin-bottom:20px;">
        <div class="form-group">
          <label>API key{% if groq_key_set %} <span style="color:#27ae60; font-weight:600;">✓ задан</span>{% else %} <span style="color:#e74c3c; font-weight:600;">не задан</span>{% endif %}</label>
          <input type="password" id="groqApiKeyInput" placeholder="gsk_..." autocomplete="off" style="font-family:monospace;">
        </div>
        <button type="submit" class="btn btn-primary" id="saveGroqKeyBtn" style="margin-top:12px;">Сохранить ключ</button>
        <span id="groqKeyMsg" class="help" style="margin-left:10px; display:none;"></span>
      </form>

      <hr style="border:none; border-top:1px solid var(--card-border); margin:0 0 20px;">
      <p class="help">Промпты подставляются перед стандартной инструкцией модели. Сохраняются в config.json.</p>
      <label>Промпт: итоги встречи (свободный текст)</label>
      <select id="meetingPromptVariant" style="width:100%; margin-bottom:6px;"><option value="">— текущий —</option></select>
      <textarea id="meetingPromptText" rows="6" style="width:100%; margin-bottom:12px;">{{ default_meeting_prompt | e }}</textarea>
      <div style="margin-bottom:16px;">
        <button type="button" class="btn btn-secondary" id="saveMeetingPromptBtn">Сохранить</button>
        <input type="text" id="meetingVariantName" placeholder="Название варианта" style="margin-left:8px; padding:6px; width:160px;">
        <button type="button" class="btn btn-secondary" id="saveMeetingPromptVariantBtn">Сохранить как вариант</button>
      </div>
      <hr style="border:none; border-top:1px solid var(--card-border); margin:16px 0;">
      <label>Промпт: транскрипция звонка</label>
      <select id="transcriptionPromptVariant" style="width:100%; margin-bottom:6px;"><option value="">— текущий —</option></select>
      <textarea id="transcriptionPromptText" rows="6" style="width:100%; margin-bottom:12px;">{{ default_transcription_prompt | e }}</textarea>
      <div>
        <button type="button" class="btn btn-secondary" id="saveTranscriptionPromptBtn">Сохранить</button>
        <input type="text" id="transcriptionVariantName" placeholder="Название варианта" style="margin-left:8px; padding:6px; width:160px;">
        <button type="button" class="btn btn-secondary" id="saveTranscriptionPromptVariantBtn">Сохранить как вариант</button>
      </div>
      <hr style="border:none; border-top:1px solid var(--card-border); margin:20px 0;">
      <h4 style="margin:0 0 10px; color:var(--accent);">🔑 Roadmap API</h4>
      <p class="help">Вы вошли как: <b>{{ creds_login or '—' }}</b>. Чтобы сменить аккаунт — <a href="/logout" style="color:var(--accent);">выйдите</a> и войдите заново.</p>
    </div>
  </div>
  <script>
    function applyMode(val) {
      var modeMeeting = document.getElementById('modeMeeting');
      var modeBulk = document.getElementById('modeBulk');
      if (!modeMeeting || !modeBulk) return;
      var isBulk = (val === 'bulk');
      modeMeeting.style.display = isBulk ? 'none' : 'block';
      modeBulk.style.display = isBulk ? 'block' : 'none';
      document.querySelectorAll('.mode-tab').forEach(function(t) {
        t.classList.toggle('active', t.getAttribute('data-mode') === val);
      });
      if (isBulk) modeBulk.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  </script>
  <script>
    {% if creds_ok %}
    var TEAM_OPTIONS = {{ team_choices | tojson }};
    var TASK_TYPE_OPTIONS = {{ task_type_choices | tojson }};
    {% else %}
    var TEAM_OPTIONS = [];
    var TASK_TYPE_OPTIONS = [];
    {% endif %}
    var DEFAULT_MEETING_PROMPT = {{ default_meeting_prompt | tojson }};
    var DEFAULT_TRANSCRIPTION_PROMPT = {{ default_transcription_prompt | tojson }};
    document.addEventListener('DOMContentLoaded', function() {
    if (window.location.search.indexOf('demo=1') !== -1) {
      var ms = document.getElementById('meetingSummary');
      var sid = document.getElementById('kvenSiteIdsInput');
      if (ms) ms.value = 'Обсудили ранжирование поиска. Партнёр подготовит список брендов к следующей неделе. Мы проверим трекинг событий.';
      if (sid) sid.value = '221';
    }
    document.getElementById('siteIdsFile')?.addEventListener('change', function() {
      var dz = document.getElementById('dropZone');
      if (dz) dz.classList.toggle('has-files', this.files && this.files.length > 0);
    });
    function setupDropZone(zoneId, inputId, acceptTxtOnly, onFile) {
      var zone = document.getElementById(zoneId);
      var input = document.getElementById(inputId);
      if (!zone || !input) return;
      zone.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'copy';
        zone.classList.add('has-files', 'drag-over');
      }, true);
      zone.addEventListener('dragleave', function(e) {
        e.preventDefault();
        e.stopPropagation();
        if (!zone.contains(e.relatedTarget)) { zone.classList.remove('has-files', 'drag-over'); }
      }, true);
      zone.addEventListener('drop', function(e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove('has-files', 'drag-over');
        var files = e.dataTransfer && e.dataTransfer.files;
        if (!files || !files.length) return;
        var f = files[0];
        if (acceptTxtOnly && !f.name.toLowerCase().endsWith('.txt')) { alert('Нужен файл .txt'); return; }
        if (onFile) { onFile(f); return; }
        var dt = new DataTransfer();
        dt.items.add(f);
        input.files = dt.files;
        zone.classList.add('has-files');
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }, true);
      zone.addEventListener('click', function(e) { var tag = (e.target.tagName || '').toLowerCase(); if (tag === 'textarea' || tag === 'input' || tag === 'select' || tag === 'button' || tag === 'a') return; input.click(); });
    }
    setupDropZone('dropZone', 'siteIdsFile', false, null);
    if (document.getElementById('modeMeeting') && document.getElementById('modeBulk')) applyMode('meeting');
    var f = document.getElementById('f');
    var loadOverlay = document.getElementById('loadOverlay');
    var submitBtn = document.getElementById('submitBtn');
    if (f && loadOverlay && submitBtn) {
      f.addEventListener('submit', function(e) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Отправка…';
        loadOverlay.classList.add('visible');
        /* не preventDefault — форма уходит на сервер */
      });
    }
    var credsForm = document.getElementById('credsForm');
    if (credsForm) {
      credsForm.addEventListener('submit', function(e) {
        e.preventDefault();
        var btn = document.getElementById('credsSubmitBtn');
        var msg = document.getElementById('credsMsg');
        if (btn) btn.disabled = true;
        if (msg) { msg.style.display = 'block'; msg.textContent = 'Сохранение…'; msg.style.color = 'var(--muted)'; }
        fetch('/api/save_creds', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            login: document.getElementById('credsLogin').value.trim(),
            password: document.getElementById('credsPassword').value
          })
        }).then(function(r) { return r.json().then(function(data) { return { ok: r.ok, data: data }; }); })
          .then(function(result) {
            if (result.ok && result.data.saved) {
              if (msg) msg.textContent = 'Сохранено. Перезагружаю…';
              msg.style.color = 'var(--accent)';
              setTimeout(function() { location.reload(); }, 500);
            } else {
              if (msg) msg.textContent = result.data.error || 'Ошибка сохранения';
              msg.style.color = 'var(--accent)';
              if (btn) btn.disabled = false;
            }
          }).catch(function(err) {
            if (msg) msg.textContent = 'Ошибка: ' + err.message;
            msg.style.color = 'var(--accent)';
            if (btn) btn.disabled = false;
          });
      });
    }
    var toggleCredsBtn = document.getElementById('toggleCredsBtn');
    var credsEditBlock = document.getElementById('credsEditBlock');
    if (toggleCredsBtn && credsEditBlock) {
      toggleCredsBtn.addEventListener('click', function() {
        var show = credsEditBlock.style.display === 'none';
        credsEditBlock.style.display = show ? 'block' : 'none';
        toggleCredsBtn.textContent = show ? 'Скрыть креды' : 'Изменить креды API';
      });
    }
    var groqKeyForm = document.getElementById('groqKeyForm');
    if (groqKeyForm) {
      groqKeyForm.addEventListener('submit', function(e) {
        e.preventDefault();
        var msg = document.getElementById('groqKeyMsg');
        var btn = document.getElementById('saveGroqKeyBtn');
        var key = (document.getElementById('groqApiKeyInput').value || '').trim();
        if (!key) { if (msg) { msg.style.display='inline'; msg.textContent='Введите ключ'; msg.style.color='var(--accent)'; } return; }
        if (btn) btn.disabled = true;
        if (msg) { msg.style.display='inline'; msg.textContent='Сохранение…'; msg.style.color='var(--muted)'; }
        fetch('/api/save_creds', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ grok_api_key: key })
        }).then(function(r) { return r.json().then(function(d) { return {ok:r.ok,data:d}; }); })
          .then(function(result) {
            if (result.ok && result.data.saved) {
              if (msg) { msg.textContent='Ключ сохранён ✓'; msg.style.color='#27ae60'; }
              document.getElementById('groqApiKeyInput').value = '';
              setTimeout(function() { location.reload(); }, 800);
            } else {
              if (msg) { msg.textContent = result.data.error || 'Ошибка'; msg.style.color='var(--accent)'; }
              if (btn) btn.disabled = false;
            }
          }).catch(function(err) {
            if (msg) { msg.textContent='Ошибка: '+err.message; msg.style.color='var(--accent)'; }
            if (btn) btn.disabled = false;
          });
      });
    }
    var credsFormEdit = document.getElementById('credsFormEdit');
    if (credsFormEdit) {
      credsFormEdit.addEventListener('submit', function(e) {
        e.preventDefault();
        var msg = document.getElementById('credsEditMsg');
        if (msg) { msg.style.display = 'inline'; msg.textContent = 'Сохранение…'; msg.style.color = 'var(--muted)'; }
        fetch('/api/save_creds', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            login: document.getElementById('credsEditLogin').value.trim(),
            password: document.getElementById('credsEditPassword').value
          })
        }).then(function(r) { return r.json().then(function(data) { return { ok: r.ok, data: data }; }); })
          .then(function(result) {
            if (result.ok && result.data.saved) {
              if (msg) { msg.textContent = 'Сохранено ✓'; msg.style.color='#27ae60'; }
              setTimeout(function() { location.reload(); }, 600);
            } else {
              if (msg) { msg.textContent = result.data.error || 'Ошибка'; msg.style.color = 'var(--accent)'; }
            }
          }).catch(function(err) {
            if (msg) { msg.textContent = 'Ошибка: ' + err.message; msg.style.color = 'var(--accent)'; }
          });
      });
    }
    // Groq: итоги встречи → задачи
    var kvenGenerateBtn = document.getElementById('kvenGenerateBtn');
    var kvenCancelBtn = document.getElementById('kvenCancelBtn');
    var kvenPreviewCard = document.getElementById('kvenPreviewCard');
    var kvenTasksList = document.getElementById('kvenTasksList');
    var kvenSiteIds = document.getElementById('kvenSiteIds');
    var kvenSendBtn = document.getElementById('kvenSendBtn');
    var kvenStatus = document.getElementById('kvenStatus');
    var kvenSendStatus = document.getElementById('kvenSendStatus');
    var kvenSendResult = document.getElementById('kvenSendResult');
    var kvenModelLabel = document.getElementById('kvenModelLabel');
    var kvenTasksData = [];
    var _kvenAbortCtrl = null;
    var _kvenTimerInterval = null;

    // Показываем имя модели при загрузке страницы
    fetch('/api/ollama-model').then(function(r){ return r.json(); }).then(function(d){
      if (kvenModelLabel && d.model) kvenModelLabel.textContent = 'Модель: ' + d.model;
    }).catch(function(){});

    function _kvenStartTimer(statusEl) {
      var start = Date.now();
      if (_kvenTimerInterval) clearInterval(_kvenTimerInterval);
      _kvenTimerInterval = setInterval(function() {
        var sec = Math.round((Date.now() - start) / 1000);
        if (statusEl) statusEl.textContent = 'Генерация… ' + sec + ' с';
      }, 1000);
    }
    function _kvenStopTimer() {
      if (_kvenTimerInterval) { clearInterval(_kvenTimerInterval); _kvenTimerInterval = null; }
    }
    function _kvenSetGenerating(isGenerating) {
      if (kvenGenerateBtn) kvenGenerateBtn.disabled = isGenerating;
      if (kvenCancelBtn) kvenCancelBtn.style.display = isGenerating ? 'inline-flex' : 'none';
      if (!isGenerating) _kvenStopTimer();
    }

    function doDirectKvenGenerate(text) {
      _kvenSetGenerating(true);
      if (kvenStatus) { kvenStatus.textContent = 'Генерация…'; kvenStatus.style.color = 'var(--muted)'; }
      _kvenStartTimer(kvenStatus);
      _kvenAbortCtrl = new AbortController();
      fetch('/api/generate-tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text }),
        signal: _kvenAbortCtrl.signal
      }).then(function(r) {
        return r.json().then(function(d) { return { ok: r.ok, data: d }; }).catch(function() {
          return { ok: false, data: { error: 'Ответ сервера не JSON (' + r.status + ')' } };
        });
      }).then(function(result) {
        _kvenSetGenerating(false);
        if (!result.ok) {
          if (kvenStatus) { kvenStatus.textContent = result.data && result.data.error ? result.data.error : 'Ошибка запроса'; kvenStatus.style.color = 'var(--accent)'; }
          return;
        }
        kvenTasksData = result.data.tasks || [];
        renderKvenTasks();
        if (kvenPreviewCard) kvenPreviewCard.style.display = 'block';
        var siteIdsInput = document.getElementById('kvenSiteIdsInput');
        if (siteIdsInput && siteIdsInput.value.trim() && kvenSiteIds) kvenSiteIds.value = siteIdsInput.value.trim();
        if (kvenStatus) { kvenStatus.textContent = 'Готово. Задач: ' + kvenTasksData.length; kvenStatus.style.color = 'var(--text)'; }
        if (kvenSendResult) kvenSendResult.style.display = 'none';
      }).catch(function(err) {
        _kvenSetGenerating(false);
        if (err.name === 'AbortError') {
          if (kvenStatus) { kvenStatus.textContent = 'Отменено'; kvenStatus.style.color = 'var(--muted)'; }
        } else {
          if (kvenStatus) { kvenStatus.textContent = 'Ошибка: ' + (err.message || 'нет ответа'); kvenStatus.style.color = 'var(--accent)'; }
        }
      });
    }
    function _runTranscriptionProcess() {
      // Автоматически запустить обработку транскрипции (как клик по кнопке)
      if (transcriptionProcessBtn && !transcriptionProcessBtn.disabled) {
        transcriptionProcessBtn.click();
      }
    }
    function runKvenGenerate() {
      var meetingSummary = document.getElementById('meetingSummary');
      var text = meetingSummary ? meetingSummary.value.trim() : '';
      if (!text) {
        if (kvenStatus) { kvenStatus.textContent = 'Введите текст в поле выше или загрузите .txt в зону'; kvenStatus.style.color = 'var(--accent)'; kvenStatus.style.visibility = 'visible'; }
        return;
      }
      if (kvenStatus) { kvenStatus.textContent = 'Анализ текста…'; kvenStatus.style.color = 'var(--muted)'; kvenStatus.style.visibility = 'visible'; kvenStatus.style.display = 'inline-block'; }
      if (kvenGenerateBtn) kvenGenerateBtn.disabled = true;
      applyTranscriptionText(text, function(meta, ok) {
        if (ok && meta && meta.speakers && meta.speakers.length > 0) {
          // Транскрипция с участниками — скопировать site_id и сразу запустить обработку
          var siteIdsInput = document.getElementById('kvenSiteIdsInput');
          var tSiteIds = document.getElementById('transcriptionSiteIds');
          if (siteIdsInput && tSiteIds && siteIdsInput.value.trim()) tSiteIds.value = siteIdsInput.value.trim();
          if (kvenGenerateBtn) kvenGenerateBtn.disabled = false;
          var block = document.getElementById('transcriptionMetaBlock');
          if (block) block.scrollIntoView({ behavior: 'smooth', block: 'start' });
          // Автоматически запускаем обработку — не требуем второй клик
          if (kvenStatus) { kvenStatus.textContent = 'Запуск обработки транскрипции…'; kvenStatus.style.color = 'var(--muted)'; }
          _runTranscriptionProcess();
        } else {
          doDirectKvenGenerate(text);
        }
      });
    }
    window.runKvenGenerate = runKvenGenerate;
    if (kvenGenerateBtn) {
      kvenGenerateBtn.addEventListener('click', function(e) { e.preventDefault(); if (kvenStatus) { kvenStatus.textContent = 'Запуск…'; kvenStatus.style.visibility = 'visible'; kvenStatus.style.display = 'inline-block'; } runKvenGenerate(); });
    }
    if (kvenCancelBtn) {
      kvenCancelBtn.addEventListener('click', function() {
        if (_kvenAbortCtrl) _kvenAbortCtrl.abort();
        fetch('/api/cancel-generation', { method: 'POST' }).catch(function(){});
        _kvenSetGenerating(false);
        if (kvenStatus) { kvenStatus.textContent = 'Отменено'; kvenStatus.style.color = 'var(--muted)'; }
      });
    }
    function renderKvenTasks() {
      if (!kvenTasksList) return;
      kvenTasksList.innerHTML = '';
      kvenTasksData.forEach(function(t, i) {
        var row = document.createElement('div');
        row.className = 'card';
        row.style.marginTop = '8px';
        row.style.padding = '12px';
        var statusOpts = [
          ['plan','📋 План'],['in_progress','🔄 В работе'],['discussion','💬 Обсуждается'],
          ['done','✅ Готово'],['blocker','🚫 Блокер'],['pending','⏸ Ожидание']
        ];
        var curStatus = t.status || 'plan';
        row.innerHTML =
          '<label style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">' +
          '<input type="checkbox" class="kven-task-cb" data-i="' + i + '" checked> ' +
          '<input type="text" class="kven-task-title" data-i="' + i + '" value="' + (t.title || '').replace(/"/g, '&quot;') + '" placeholder="Название" style="flex:1;font-weight:600;">' +
          '<select class="kven-task-assignee" data-i="' + i + '" style="min-width:110px;"><option value="any"' + (t.assignee === 'partner' ? '' : ' selected') + '>Диджинетика</option><option value="partner"' + (t.assignee === 'partner' ? ' selected' : '') + '>Партнёр</option></select>' +
          '</label>' +
          '<textarea class="kven-task-desc" data-i="' + i + '" placeholder="Описание задачи" rows="2" style="width:100%;margin-bottom:8px;">' + (t.description || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/&/g, '&amp;') + '</textarea>' +
          '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">' +
          '<select class="kven-task-status" data-i="' + i + '" style="min-width:140px;">' +
          statusOpts.map(function(s){ return '<option value="'+s[0]+'"'+(curStatus===s[0]?' selected':'')+'>'+s[1]+'</option>'; }).join('') +
          '</select>' +
          '<select class="kven-task-team" data-i="' + i + '"><option value="">— Команда</option>' +
          (typeof TEAM_OPTIONS !== 'undefined' ? TEAM_OPTIONS.map(function(v){ return '<option value="'+v+'"'+(t.team===v?' selected':'')+'>'+v+'</option>'; }).join('') : '') +
          '</select>' +
          '<select class="kven-task-type" data-i="' + i + '"><option value="">— Тип</option>' +
          (typeof TASK_TYPE_OPTIONS !== 'undefined' ? TASK_TYPE_OPTIONS.map(function(v){ return '<option value="'+v+'"'+(t.task_type===v?' selected':'')+'>'+v+'</option>'; }).join('') : '') +
          '</select>' +
          '</div>';
        kvenTasksList.appendChild(row);
      });
    }
    if (kvenSendBtn && kvenTasksList) {
      kvenSendBtn.addEventListener('click', function() {
        var siteIdsText = (kvenSiteIds && kvenSiteIds.value || '').trim();
        var ids = siteIdsText.split(/[,\\s;]+/).map(function(s){ return s.trim(); }).filter(Boolean);
        if (!ids.length) { kvenSendStatus.textContent = 'Укажите хотя бы один site_id'; kvenSendStatus.style.color = 'var(--accent)'; return; }
        var selected = [];
        kvenTasksList.querySelectorAll('.kven-task-cb:checked').forEach(function(cb) {
          var i = parseInt(cb.getAttribute('data-i'), 10);
          if (isNaN(i) || !kvenTasksData[i]) return;
          var titleEl = kvenTasksList.querySelector('.kven-task-title[data-i="'+i+'"]');
          var descEl = kvenTasksList.querySelector('.kven-task-desc[data-i="'+i+'"]');
          var assigneeEl = kvenTasksList.querySelector('.kven-task-assignee[data-i="'+i+'"]');
          var statusEl = kvenTasksList.querySelector('.kven-task-status[data-i="'+i+'"]');
          var teamEl = kvenTasksList.querySelector('.kven-task-team[data-i="'+i+'"]');
          var typeEl = kvenTasksList.querySelector('.kven-task-type[data-i="'+i+'"]');
          selected.push({
            title: titleEl ? titleEl.value.trim() : kvenTasksData[i].title,
            description: descEl ? descEl.value.trim() : kvenTasksData[i].description,
            status: statusEl ? statusEl.value : (kvenTasksData[i].status || 'plan'),
            priority: kvenTasksData[i].priority || 'medium',
            assignee: assigneeEl ? assigneeEl.value : (kvenTasksData[i].assignee || 'any'),
            team: teamEl ? teamEl.value : (kvenTasksData[i].team || ''),
            task_type: typeEl ? typeEl.value : (kvenTasksData[i].task_type || ''),
            product: kvenTasksData[i].product || '',
            due_date: kvenTasksData[i].due_date || '',
            link: kvenTasksData[i].link || ''
          });
        });
        if (!selected.length) { kvenSendStatus.textContent = 'Отметьте хотя бы одну задачу'; kvenSendStatus.style.color = 'var(--accent)'; return; }
        kvenSendStatus.textContent = 'Отправка…';
        kvenSendStatus.style.color = 'var(--muted)';
        kvenSendBtn.disabled = true;
        kvenSendResult.style.display = 'none';
        fetch('/api/send_tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tasks: selected, site_ids: ids, meeting_summary: (document.getElementById('meetingSummary') || {}).value.trim() })
        }).then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
          .then(function(result) {
            kvenSendBtn.disabled = false;
            kvenSendResult.style.display = 'block';
            if (result.ok && result.data.ok) {
              kvenSendResult.style.background = 'rgba(46, 160, 67, 0.2)';
              kvenSendResult.style.border = '1px solid rgba(46, 160, 67, 0.5)';
              kvenSendResult.textContent = 'Создано задач: ' + (result.data.total_created || 0) + '\\n' + (result.data.lines || []).join('\\n');
              kvenSendStatus.textContent = 'Готово';
              kvenSendStatus.style.color = 'var(--text)';
            } else {
              kvenSendResult.style.background = 'rgba(248, 81, 73, 0.15)';
              kvenSendResult.style.border = '1px solid rgba(248, 81, 73, 0.4)';
              kvenSendResult.textContent = result.data.error || (result.data.lines || []).join('\\n') || 'Ошибка';
              kvenSendStatus.textContent = 'Ошибка';
              kvenSendStatus.style.color = 'var(--accent)';
            }
          }).catch(function(err) {
            kvenSendBtn.disabled = false;
            kvenSendResult.style.display = 'block';
            kvenSendResult.style.background = 'rgba(248, 81, 73, 0.15)';
            kvenSendResult.style.border = '1px solid rgba(248, 81, 73, 0.4)';
            kvenSendResult.textContent = 'Ошибка: ' + (err.message || 'нет ответа');
            kvenSendStatus.textContent = 'Ошибка';
            kvenSendStatus.style.color = 'var(--accent)';
          });
      });
    }
    // —— Промпт (настройки) ——
    var promptModal = document.getElementById('promptModal');
    var promptSettingsBtn = document.getElementById('promptSettingsBtn');
    if (promptSettingsBtn && promptModal) {
      promptSettingsBtn.addEventListener('click', function() {
        promptModal.style.display = 'flex';
        fetch('/api/meeting-prompt').then(function(r){ return r.json(); }).then(function(d) {
          document.getElementById('meetingPromptText').value = (d.meeting_prompt && d.meeting_prompt.trim()) ? d.meeting_prompt : (typeof DEFAULT_MEETING_PROMPT !== 'undefined' ? DEFAULT_MEETING_PROMPT : '');
          document.getElementById('transcriptionPromptText').value = (d.transcription_prompt && d.transcription_prompt.trim()) ? d.transcription_prompt : (typeof DEFAULT_TRANSCRIPTION_PROMPT !== 'undefined' ? DEFAULT_TRANSCRIPTION_PROMPT : '');
          var selM = document.getElementById('meetingPromptVariant');
          selM.innerHTML = '<option value="">— текущий —</option>';
          (d.meeting_prompt_variants || []).forEach(function(v, i) {
            var o = document.createElement('option');
            o.value = i;
            o.textContent = v.name || 'Вариант ' + (i+1);
            selM.appendChild(o);
          });
          var selT = document.getElementById('transcriptionPromptVariant');
          selT.innerHTML = '<option value="">— текущий —</option>';
          (d.transcription_prompt_variants || []).forEach(function(v, i) {
            var o = document.createElement('option');
            o.value = i;
            o.textContent = v.name || 'Вариант ' + (i+1);
            selT.appendChild(o);
          });
        });
      });
      document.getElementById('promptModalClose').addEventListener('click', function() { promptModal.style.display = 'none'; });
      promptModal.addEventListener('click', function(e) { if (e.target === promptModal) promptModal.style.display = 'none'; });
      function saveMeetingPrompt() {
        fetch('/api/meeting-prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ meeting_prompt: document.getElementById('meetingPromptText').value }) }).then(function(r){ return r.json(); }).then(function(d) { if (d.error) alert(d.error); else promptModal.style.display = 'none'; });
      }
      function saveTranscriptionPrompt() {
        fetch('/api/meeting-prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ transcription_prompt: document.getElementById('transcriptionPromptText').value }) }).then(function(r){ return r.json(); }).then(function(d) { if (d.error) alert(d.error); else promptModal.style.display = 'none'; });
      }
      document.getElementById('saveMeetingPromptBtn').addEventListener('click', saveMeetingPrompt);
      document.getElementById('saveTranscriptionPromptBtn').addEventListener('click', saveTranscriptionPrompt);
      document.getElementById('saveMeetingPromptVariantBtn').addEventListener('click', function() {
        fetch('/api/meeting-prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ meeting_prompt: document.getElementById('meetingPromptText').value, save_meeting_as_variant: true, meeting_variant_name: document.getElementById('meetingVariantName').value || 'Вариант' }) }).then(function(r){ return r.json(); }).then(function(d) { if (d.error) alert(d.error); else { promptModal.style.display = 'none'; } });
      });
      document.getElementById('saveTranscriptionPromptVariantBtn').addEventListener('click', function() {
        fetch('/api/meeting-prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ transcription_prompt: document.getElementById('transcriptionPromptText').value, save_transcription_as_variant: true, transcription_variant_name: document.getElementById('transcriptionVariantName').value || 'Вариант' }) }).then(function(r){ return r.json(); }).then(function(d) { if (d.error) alert(d.error); else { promptModal.style.display = 'none'; } });
      });
      document.getElementById('meetingPromptVariant').addEventListener('change', function() {
        var i = parseInt(this.value, 10);
        if (i >= 0) fetch('/api/meeting-prompt').then(function(r){ return r.json(); }).then(function(d) { var v = (d.meeting_prompt_variants || [])[i]; if (v && v.text !== undefined) document.getElementById('meetingPromptText').value = v.text; });
      });
      document.getElementById('transcriptionPromptVariant').addEventListener('change', function() {
        var i = parseInt(this.value, 10);
        if (i >= 0) fetch('/api/meeting-prompt').then(function(r){ return r.json(); }).then(function(d) { var v = (d.transcription_prompt_variants || [])[i]; if (v && v.text !== undefined) document.getElementById('transcriptionPromptText').value = v.text; });
      });
    }
    // —— Транскрипция ——
    var transcriptionMetaBlock = document.getElementById('transcriptionMetaBlock');
    var transcriptionResultBlock = document.getElementById('transcriptionResultBlock');
    var transcriptionRawText = '';
    var transcriptionMeta = null;
    function applyTranscriptionText(text, onComplete) {
      transcriptionRawText = (text || '').replace(/\\r\\n/g, '\\n');
      fetch('/api/transcription-metadata', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: transcriptionRawText }) }).then(function(res){ return res.json(); }).then(function(meta) {
        if (meta.error) { if (onComplete) { onComplete(meta, false); } else { alert(meta.error); } return; }
        transcriptionMeta = meta;
        document.getElementById('transcriptionTitleHint').textContent = (meta.suggested_partner_side && meta.suggested_our_side) ? ('Подсказка: «' + meta.suggested_our_side + '» — скорее всего мы (Any), «' + meta.suggested_partner_side + '» — партнёр.') : '';
        var wrap = document.getElementById('transcriptionSpeakersCheckboxes');
        wrap.innerHTML = '';
        (meta.speakers || []).forEach(function(name) {
          var label = document.createElement('label');
          label.style.display = 'flex';
          label.style.alignItems = 'center';
          label.style.gap = '6px';
          label.style.cursor = 'pointer';
          var cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.className = 'transcription-any-cb';
          cb.setAttribute('data-name', name);
          if (meta.suggested_our_side && name.toLowerCase().indexOf(meta.suggested_our_side.toLowerCase().split(/[\\s&]/)[0]) >= 0) cb.checked = true;
          label.appendChild(cb);
          label.appendChild(document.createTextNode(name));
          wrap.appendChild(label);
        });
        transcriptionMetaBlock.style.display = 'block';
        transcriptionResultBlock.style.display = 'none';
        if (onComplete) { onComplete(meta, true); } else {
          var block = document.getElementById('transcriptionMetaBlock');
          if (block) block.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }).catch(function(e) { if (onComplete) { onComplete(null, false); } else { alert('Ошибка: ' + (e.message || e)); } });
    }
    function onTranscriptFile(f) {
      var r = new FileReader();
      r.onload = function() {
        var text = r.result;
        var meetingSummary = document.getElementById('meetingSummary');
        if (meetingSummary) meetingSummary.value = text;
        applyTranscriptionText(text);
      };
      r.readAsText(f, 'UTF-8');
    }
    var meetingSummaryEl = document.getElementById('meetingSummary');
    if (meetingSummaryEl) {
      meetingSummaryEl.addEventListener('dragover', function(e) { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy'; meetingSummaryEl.style.borderColor = 'var(--accent-dim)'; meetingSummaryEl.style.background = 'rgba(255, 107, 107, 0.06)'; });
      meetingSummaryEl.addEventListener('dragleave', function(e) { e.preventDefault(); e.stopPropagation(); meetingSummaryEl.style.borderColor = ''; meetingSummaryEl.style.background = ''; });
      meetingSummaryEl.addEventListener('drop', function(e) { e.preventDefault(); e.stopPropagation(); meetingSummaryEl.style.borderColor = ''; meetingSummaryEl.style.background = ''; var files = e.dataTransfer && e.dataTransfer.files; if (!files || !files.length) return; var f = files[0]; if (!f.name.toLowerCase().endsWith('.txt')) { alert('Нужен файл .txt'); return; } onTranscriptFile(f); });
    }
    var kvenFileInput = document.getElementById('kvenTranscriptionFile');
    var kvenChooseFileBtn = document.getElementById('kvenChooseFileBtn');
    if (kvenChooseFileBtn && kvenFileInput) kvenChooseFileBtn.addEventListener('click', function() { kvenFileInput.click(); });
    if (kvenFileInput) kvenFileInput.addEventListener('change', function() {
      var f = this.files && this.files[0];
      if (!f) return;
      var nameEl = document.getElementById('kvenFileName');
      if (nameEl) nameEl.textContent = f.name;
      onTranscriptFile(f);
      this.value = '';
    });
    var transcriptionProcessBtn = document.getElementById('transcriptionProcessBtn');
    var transcriptionCancelBtn = document.getElementById('transcriptionCancelBtn');
    var _transcriptionAbortCtrl = null;
    var _transcriptionTimerInterval = null;
    function _transcriptionStartTimer() {
      var start = Date.now();
      var statusEl = document.getElementById('transcriptionStatus');
      if (_transcriptionTimerInterval) clearInterval(_transcriptionTimerInterval);
      _transcriptionTimerInterval = setInterval(function() {
        var sec = Math.round((Date.now() - start) / 1000);
        if (statusEl) statusEl.textContent = 'Генерация… ' + sec + ' с';
      }, 1000);
    }
    function _transcriptionStopTimer() {
      if (_transcriptionTimerInterval) { clearInterval(_transcriptionTimerInterval); _transcriptionTimerInterval = null; }
    }
    function _transcriptionSetGenerating(isGenerating) {
      if (transcriptionProcessBtn) transcriptionProcessBtn.disabled = isGenerating;
      if (transcriptionCancelBtn) transcriptionCancelBtn.style.display = isGenerating ? 'inline-flex' : 'none';
      if (!isGenerating) _transcriptionStopTimer();
    }
    if (transcriptionProcessBtn) {
      transcriptionProcessBtn.addEventListener('click', function() {
        var partnerSpeakers = [];
        document.querySelectorAll('.transcription-any-cb').forEach(function(cb) {
          var name = cb.getAttribute('data-name');
          if (name && !cb.checked) partnerSpeakers.push(name);
        });
        var statusEl = document.getElementById('transcriptionStatus');
        if (statusEl) statusEl.textContent = 'Обработка…';
        _transcriptionSetGenerating(true);
        _transcriptionStartTimer();
        _transcriptionAbortCtrl = new AbortController();
        fetch('/api/process-transcription', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: transcriptionRawText, partner_speakers: partnerSpeakers }),
          signal: _transcriptionAbortCtrl.signal
        }).then(function(r){ return r.json().then(function(d){ return { ok: r.ok, data: d }; }); }).then(function(result) {
          _transcriptionSetGenerating(false);
          if (!result.ok || result.data.error) {
            if (statusEl) { statusEl.textContent = result.data.error || 'Ошибка'; statusEl.style.color = 'var(--accent)'; }
            return;
          }
          document.getElementById('transcriptionSummary').textContent = result.data.summary || '';
          document.getElementById('transcriptionPostMessage').value = result.data.post_meeting_message || '';
          var list = document.getElementById('transcriptionTasksList');
          list.innerHTML = '';
          (result.data.tasks || []).forEach(function(t, i) {
            var row = document.createElement('div');
            row.className = 'card';
            row.style.marginTop = '8px';
            row.style.padding = '12px';
            row.innerHTML =
              '<label style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">' +
              '<input type="checkbox" class="transcription-task-cb" checked data-i="' + i + '"> ' +
              '<input type="text" class="transcription-task-title" data-i="' + i + '" value="' + (t.title || '').replace(/"/g, '&quot;').replace(/</g, '&lt;') + '" placeholder="Название задачи" style="flex:1;font-weight:600;">' +
              '<select class="transcription-task-assignee" data-i="' + i + '" style="min-width:110px;"><option value="any"' + (t.assignee === 'partner' ? '' : ' selected') + '>Диджинетика</option><option value="partner"' + (t.assignee === 'partner' ? ' selected' : '') + '>Партнёр</option></select>' +
              '</label>' +
              '<textarea class="transcription-task-desc" data-i="' + i + '" rows="2" style="width:100%;margin-bottom:0;">' + (t.description || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/&/g, '&amp;') + '</textarea>';
            list.appendChild(row);
          });
          window._transcriptionTasksData = result.data.tasks || [];
          if (statusEl) { statusEl.textContent = 'Готово. Задач: ' + (result.data.tasks || []).length; statusEl.style.color = 'var(--text)'; }
          transcriptionResultBlock.style.display = 'block';
        }).catch(function(e) {
          _transcriptionSetGenerating(false);
          if (statusEl) {
            if (e.name === 'AbortError') { statusEl.textContent = 'Отменено'; statusEl.style.color = 'var(--muted)'; }
            else { statusEl.textContent = 'Ошибка: ' + (e.message || e); statusEl.style.color = 'var(--accent)'; }
          }
        });
      });
    }
    if (transcriptionCancelBtn) {
      transcriptionCancelBtn.addEventListener('click', function() {
        if (_transcriptionAbortCtrl) _transcriptionAbortCtrl.abort();
        fetch('/api/cancel-generation', { method: 'POST' }).catch(function(){});
        _transcriptionSetGenerating(false);
        var statusEl = document.getElementById('transcriptionStatus');
        if (statusEl) { statusEl.textContent = 'Отменено'; statusEl.style.color = 'var(--muted)'; }
      });
    }
    document.getElementById('transcriptionCopyMsgBtn').addEventListener('click', function() {
      var ta = document.getElementById('transcriptionPostMessage');
      var t = ta.value;
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(function(){ ta.select(); });
      else { ta.select(); document.execCommand('copy'); }
    });
    var transcriptionSendTasksBtn = document.getElementById('transcriptionSendTasksBtn');
    if (transcriptionSendTasksBtn) {
      transcriptionSendTasksBtn.addEventListener('click', function() {
        var tasksData = window._transcriptionTasksData || [];
        var list = document.getElementById('transcriptionTasksList');
        var siteIdsText = (document.getElementById('transcriptionSiteIds') && document.getElementById('transcriptionSiteIds').value || '').trim();
        var ids = siteIdsText.split(/[,\\s;]+/).map(function(s){ return s.trim(); }).filter(Boolean);
        if (!ids.length) { document.getElementById('transcriptionSendResult').textContent = 'Укажите site_id'; document.getElementById('transcriptionSendResult').style.display = 'block'; return; }
        var selected = [];
        list.querySelectorAll('.transcription-task-cb:checked').forEach(function(cb) {
          var i = parseInt(cb.getAttribute('data-i'), 10);
          if (isNaN(i) || !tasksData[i]) return;
          var titleEl = list.querySelector('.transcription-task-title[data-i="'+i+'"]');
          var descEl = list.querySelector('.transcription-task-desc[data-i="'+i+'"]');
          var assigneeEl = list.querySelector('.transcription-task-assignee[data-i="'+i+'"]');
          selected.push({ title: titleEl ? titleEl.value.trim() : tasksData[i].title, description: descEl ? descEl.value : tasksData[i].description, status: 'plan', priority: 'medium', assignee: assigneeEl ? assigneeEl.value : (tasksData[i].assignee || 'any'), team: tasksData[i].team || '', task_type: tasksData[i].task_type || '', product: tasksData[i].product || '', due_date: tasksData[i].due_date || '', link: tasksData[i].link || '' });
        });
        if (!selected.length) { document.getElementById('transcriptionSendResult').textContent = 'Отметьте задачи'; document.getElementById('transcriptionSendResult').style.display = 'block'; return; }
        transcriptionSendTasksBtn.disabled = true;
        var resEl = document.getElementById('transcriptionSendResult');
        resEl.style.display = 'none';
        fetch('/api/send_tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tasks: selected, site_ids: ids, meeting_summary: (document.getElementById('transcriptionPostMessage') || {}).value.trim() }) }).then(function(r){ return r.json().then(function(d){ return { ok: r.ok, data: d }; }); }).then(function(result) {
          transcriptionSendTasksBtn.disabled = false;
          resEl.style.display = 'block';
          if (result.ok && result.data.ok) { resEl.style.background = 'rgba(46, 160, 67, 0.2)'; resEl.textContent = 'Создано: ' + (result.data.total_created || 0) + '\\n' + (result.data.lines || []).join('\\n'); }
          else { resEl.style.background = 'rgba(248, 81, 73, 0.15)'; resEl.textContent = result.data.error || 'Ошибка'; }
        }).catch(function(e) { transcriptionSendTasksBtn.disabled = false; resEl.style.display = 'block'; resEl.textContent = 'Ошибка: ' + (e.message || e); });
      });
    }
    });
    </script>
</body>
</html>
"""


@app.route("/api/save_creds", methods=["POST"])
def api_save_creds():
    try:
        data = request.get_json(force=True) or {}
        login = (data.get("login") or "").strip()
        password = data.get("password") or ""
        grok_key = (data.get("grok_api_key") or "").strip()
        if grok_key:
            username = session.get("mr_login") or None
            save_grok_api_key(grok_key, username=username)
        if not grok_key:
            return jsonify({"saved": False, "error": "Укажите Grok API key"}), 400
        return jsonify({"saved": True})
    except Exception as e:
        return jsonify({"saved": False, "error": str(e)}), 500


def _load_config():
    cfg = {}
    for p in (APP_DIR / "config.json", APP_DIR / "config.example.json"):
        if p.exists():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    return cfg if isinstance(cfg, dict) else {}


def _save_config(cfg):
    (APP_DIR / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/api/meeting-prompt", methods=["GET"])
def api_get_meeting_prompt():
    cfg = _load_config()
    meeting = (cfg.get("meeting_prompt") or "").strip()
    transcription = (cfg.get("transcription_prompt") or "").strip()
    # Показываем дефолтный промпт из кода, если в конфиге пусто — чтобы в настройках не было пустого поля
    if not meeting and MEETING_TO_TASKS_PROMPT:
        meeting = MEETING_TO_TASKS_PROMPT.strip()
    if not transcription and TRANSCRIPTION_PROMPT_TEMPLATE:
        transcription = TRANSCRIPTION_PROMPT_TEMPLATE.strip()
    return jsonify({
        "meeting_prompt": meeting,
        "transcription_prompt": transcription,
        "meeting_prompt_variants": cfg.get("meeting_prompt_variants") or [],
        "transcription_prompt_variants": cfg.get("transcription_prompt_variants") or [],
    })


@app.route("/api/meeting-prompt", methods=["POST"])
def api_save_meeting_prompt():
    try:
        data = request.get_json(force=True) or {}
        cfg = _load_config()
        if "meeting_prompt" in data:
            cfg["meeting_prompt"] = (data.get("meeting_prompt") or "").strip()
        if "transcription_prompt" in data:
            cfg["transcription_prompt"] = (data.get("transcription_prompt") or "").strip()
        if data.get("save_meeting_as_variant"):
            name = (data.get("meeting_variant_name") or "Вариант").strip()
            variants = list(cfg.get("meeting_prompt_variants") or [])
            variants.append({"name": name, "text": (data.get("meeting_prompt") or "").strip()})
            cfg["meeting_prompt_variants"] = variants[-30:]
        if data.get("save_transcription_as_variant"):
            name = (data.get("transcription_variant_name") or "Вариант").strip()
            variants = list(cfg.get("transcription_prompt_variants") or [])
            variants.append({"name": name, "text": (data.get("transcription_prompt") or "").strip()})
            cfg["transcription_prompt_variants"] = variants[-30:]
        _save_config(cfg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transcription-metadata", methods=["POST"])
def api_transcription_metadata():
    if parse_transcription_metadata is None:
        return jsonify({"error": "Модуль транскрипции не загружен"}), 500
    try:
        text = ""
        if request.content_type and "application/json" in (request.content_type or ""):
            text = (request.get_json() or {}).get("text") or ""
        else:
            f = request.files.get("file")
            text = (f.read().decode("utf-8", errors="replace") if f else "") or (request.form.get("text") or "")
        if not text.strip():
            return jsonify({"error": "Загрузите файл или вставьте текст"}), 400
        meta = parse_transcription_metadata(text.strip())
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/process-transcription", methods=["POST"])
def api_process_transcription():
    if process_transcription is None:
        return jsonify({"error": "Модуль не загружен"}), 500
    _user = session.get("mr_login")
    if not grok_available(username=_user):
        return jsonify({"error": "Groq API key не задан. Укажите в настройках."}), 503
    try:
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Нет текста"}), 400
        partner_speakers = data.get("partner_speakers") or []
        if not isinstance(partner_speakers, list):
            partner_speakers = [partner_speakers] if partner_speakers else []
        cfg = _load_config()
        prompt_prefix = (cfg.get("transcription_prompt") or "").strip()
        if prompt_prefix == (TRANSCRIPTION_PROMPT_TEMPLATE or "").strip():
            prompt_prefix = None
        from creds import load_grok_api_key
        user_api_key = load_grok_api_key(username=_user) or os.environ.get("API_GROQ", "")
        result = process_transcription(text, partner_speakers, prompt_prefix=prompt_prefix + "\n\n" if prompt_prefix else None, api_key=user_api_key or None)
        if result.get("error"):
            return jsonify({"error": result["error"]}), 503
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cancel-generation", methods=["POST"])
def api_cancel_generation():
    """Прервать текущую генерацию."""
    if _cancel_ollama_generation:
        _cancel_ollama_generation()
    return jsonify({"ok": True})


@app.route("/api/ollama-model", methods=["GET"])
def api_ollama_model():
    """Вернуть имя используемой модели."""
    model = get_model_name() if get_model_name else "grok-3-mini"
    return jsonify({"model": model})


@app.route("/api/generate-tasks", methods=["POST"])
def api_generate_tasks():
    """По тексту итогов встречи сгенерировать список задач через Grok."""
    if meeting_text_to_tasks is None:
        return jsonify({"error": "Модуль ollama_meeting не загружен"}), 500
    _user = session.get("mr_login")
    if not grok_available(username=_user):
        return jsonify({"error": "Groq API key не задан. Укажите в настройках."}), 503
    try:
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Укажите текст итогов встречи"}), 400
        cfg = _load_config()
        prompt_prefix = (cfg.get("meeting_prompt") or "").strip()
        if prompt_prefix == (MEETING_TO_TASKS_PROMPT or "").strip():
            prompt_prefix = None
        if prompt_prefix:
            prompt_prefix = prompt_prefix + "\n\n"
        from creds import load_grok_api_key
        user_api_key = load_grok_api_key(username=_user) or os.environ.get("API_GROQ", "")
        tasks = meeting_text_to_tasks(text, prompt_prefix=prompt_prefix or None, api_key=user_api_key or None)
        return jsonify({"tasks": tasks})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _create_one_task_and_copy(base_url, login, password, task_fields, site_ids_list):
    """Создать одну задачу для первого site_id, скопировать на остальные. Возвращает (total_created, lines, error)."""
    import requests
    session = requests.Session()
    r = session.post(f"{base_url}/backend-v2/auth/login", json={"username": login, "password": password}, timeout=30)
    if r.status_code != 200:
        return 0, [], f"Ошибка авторизации: {r.status_code}"
    first_sid = site_ids_list[0]
    csv_body = build_task_csv_row(task_fields)
    csv_bytes = _csv_to_bytes(csv_body)
    import_url = f"{base_url}/backend-v2/import/tasks/csv"
    files_one = {"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv; charset=utf-8")}
    try:
        r_import = session.post(import_url, data={"site_id": str(first_sid)}, files=files_one, timeout=30)
    except Exception as e:
        return 0, [], str(e)
    try:
        body_import = r_import.json()
    except Exception:
        return 0, [], f"Ответ не JSON: {r_import.status_code}"
    if r_import.status_code != 200:
        err_msg = body_import.get("message") or body_import.get("detail") or body_import.get("error") or r_import.text[:300]
        return 0, [], err_msg or "нет текста"
    created_count = body_import.get("created", 0) or 0
    if created_count < 1:
        err_msg = body_import.get("detail") or body_import.get("message") or body_import.get("error") or "API вернул created: 0"
        return 0, [], err_msg
    title = (task_fields.get("title") or "").strip()
    task_id = body_import.get("task_id") or (body_import.get("task") or {}).get("id")
    if not task_id:
        task_id = (body_import.get("task_ids") or [None])[0]
    if not task_id and body_import.get("tasks"):
        task_id = (body_import.get("tasks") or [{}])[0].get("id")
    if not task_id and body_import.get("created_tasks"):
        task_id = (body_import.get("created_tasks") or [{}])[0].get("id")
    if isinstance(task_id, dict):
        task_id = task_id.get("id")
    if not task_id and len(site_ids_list) > 1:
        r_list = session.get(
            f"{base_url}/backend-v2/roadmap",
            params={"site_id": str(first_sid), "page_size": 50, "sort_by": "created_at", "sort_order": "desc"},
            timeout=30,
        )
        if r_list.status_code == 200:
            try:
                tasks = (r_list.json() or {}).get("tasks") or []
                for t in tasks:
                    if (t.get("title") or "").strip() == title:
                        task_id = t.get("id")
                        break
            except Exception:
                pass
    lines = [f"  {first_sid}: создана 1 задача"]
    total_created = 1
    if len(site_ids_list) > 1 and task_id:
        remaining = [str(s) for s in site_ids_list[1:]]
        copy_base = COPY_API_BASE_URL.rstrip("/")
        session_copy = requests.Session()
        r_login = session_copy.post(f"{copy_base}/auth/login", json={"username": login, "password": password}, timeout=30)
        if r_login.status_code != 200:
            lines.append(f"Копирование отменено: ошибка входа в API копирования {r_login.status_code}")
            return total_created, lines, None
        r_copy = session_copy.post(f"{copy_base}/roadmap/{task_id}/copy", json={"site_ids": remaining[:100]}, timeout=30)
        try:
            body_copy = r_copy.json()
        except Exception:
            lines.append(f"Копирование: ответ не JSON — {r_copy.status_code}")
            return total_created, lines, None
        if r_copy.status_code != 200:
            err_c = body_copy.get("message") or body_copy.get("detail") or body_copy.get("error") or r_copy.text[:200]
            lines.append(f"Копирование ошибка {r_copy.status_code}: {err_c}")
        else:
            copied = body_copy.get("copied") or []
            failed = body_copy.get("failed") or []
            total_created += len(copied)
            for c in copied:
                sid = c.get("site_id") or (c.get("task") or {}).get("site_id")
                if sid:
                    lines.append(f"  {sid}: скопировано")
            for f in failed:
                lines.append(f"  {f.get('site_id', '?')}: ошибка — {f.get('error', '?')}")
    elif len(site_ids_list) > 1 and not task_id:
        lines.append("Копирование не выполнено: в ответе нет task_id")
    return total_created, lines, None


def _post_meeting_log(base_url, login, password, site_id, meeting_date_iso, summary, any_planned_actions, partner_planned_actions, recording_link=None):
    """Создать/обновить запись встречи в merchrules (backend-v2/meetings). Возвращает (ok: bool, error: str|None)."""
    import requests
    session = requests.Session()
    r = session.post(f"{base_url}/backend-v2/auth/login", json={"username": login, "password": password}, timeout=30)
    if r.status_code != 200:
        return False, f"Ошибка авторизации: {r.status_code}"
    payload = {
        "site_id": str(site_id),
        "meeting_date": meeting_date_iso,
        "summary": summary or "",
        "any_planned_actions": any_planned_actions or "",
        "partner_planned_actions": partner_planned_actions or "",
    }
    if recording_link:
        payload["recording_link"] = recording_link
    try:
        r_post = session.post(f"{base_url}/backend-v2/meetings", json=payload, timeout=30)
    except Exception as e:
        return False, str(e)
    if r_post.status_code in (200, 201):
        return True, None
    try:
        err_body = r_post.json()
        err_msg = err_body.get("message") or err_body.get("detail") or err_body.get("error") or r_post.text[:200]
    except Exception:
        err_msg = r_post.text[:200] or f"HTTP {r_post.status_code}"
    return False, err_msg


@app.route("/api/send_tasks", methods=["POST"])
def api_send_tasks():
    """Отправить несколько задач (от Квен) в дашборд для указанных site_id."""
    try:
        data = request.get_json(force=True) or {}
        tasks = data.get("tasks") or []
        site_ids_raw = data.get("site_ids") or []
        if not isinstance(tasks, list) or not tasks:
            return jsonify({"error": "Укажите список задач (tasks)"}), 400
        site_ids = sorted(set(str(s).strip() for s in site_ids_raw if str(s).strip()), key=lambda x: (int(x) if x.isdigit() else 0, x))
        if not site_ids:
            return jsonify({"error": "Укажите хотя бы один site_id"}), 400
        base_url, login, password = _get_creds()
        if not base_url or not login or not password:
            return jsonify({"error": "Не авторизован"}), 401
        all_lines = []
        total_created = 0
        for i, t in enumerate(tasks):
            if not isinstance(t, dict):
                continue
            title = (t.get("title") or "").strip()
            if not title:
                continue
            task_fields = {
                "title": title,
                "description": (t.get("description") or "").strip(),
                "status": (t.get("status") or "").strip() or "plan",
                "priority": (t.get("priority") or "").strip() or "medium",
                "assignee": (t.get("assignee") or "").strip() or "any",
                "team": (t.get("team") or "").strip(),
                "task_type": (t.get("task_type") or "").strip(),
                "product": (t.get("product") or "").strip(),
                "link": (t.get("link") or "").strip(),
                "due_date": (t.get("due_date") or "").strip(),
            }
            n, lines, err = _create_one_task_and_copy(base_url, login, password, task_fields, site_ids)
            if err:
                all_lines.append(f"Задача «{title[:50]}…»: {err}")
                continue
            total_created += n
            all_lines.append(f"Задача «{title}»:")
            all_lines.extend(lines)
        # Лог встречи в merchrules: summary + наши задачи (any) и задачи партнёра (partner)
        meeting_summary = (data.get("meeting_summary") or data.get("post_meeting_message") or "").strip()
        meeting_date = (data.get("meeting_date") or "").strip()
        if not meeting_date:
            meeting_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        def _strip_leading_emoji(s: str) -> str:
            import re
            return re.sub(r'^[\U00010000-\U0010ffff\U00002600-\U000027BF\U0001F300-\U0001FAFF]\s*', '', s).strip()
        any_planned = "\n".join(_strip_leading_emoji((t.get("title") or "").strip()) for t in tasks if isinstance(t, dict) and ((t.get("assignee") or "").strip().lower() != "partner"))
        partner_planned = "\n".join(_strip_leading_emoji((t.get("title") or "").strip()) for t in tasks if isinstance(t, dict) and (t.get("assignee") or "").strip().lower() == "partner")
        recording_link = (data.get("recording_link") or "").strip() or None
        for sid in site_ids:
            ok_meeting, err_meeting = _post_meeting_log(
                base_url, login, password, sid, meeting_date, meeting_summary, any_planned, partner_planned, recording_link
            )
            if ok_meeting:
                all_lines.append(f"Встреча (site_id={sid}): лог сохранён.")
            else:
                all_lines.append(f"Встреча (site_id={sid}): {err_meeting or 'ошибка'}")
        return jsonify({
            "ok": True,
            "total_created": total_created,
            "lines": all_lines,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    base_url, login, password = _get_creds()
    creds_ok = bool(base_url and login and password)
    _username = session.get("mr_login")
    groq_key_set = bool(grok_available(username=_username)) if grok_available else False

    ctx = {
        "creds_ok": creds_ok,
        "creds_url": base_url or "",
        "creds_login": login or "",
        "groq_key_set": groq_key_set,
        "status_choices": STATUS_CHOICES,
        "priority_choices": PRIORITY_CHOICES,
        "assignee_choices": ASSIGNEE_CHOICES,
        "team_choices": TEAM_CHOICES,
        "task_type_choices": TASK_TYPE_CHOICES,
        "product_choices": PRODUCT_CHOICES,
        "msg": None,
        "msg_class": "ok",
        "default_meeting_prompt": MEETING_TO_TASKS_PROMPT if (MEETING_TO_TASKS_PROMPT is not None) else "",
        "default_transcription_prompt": TRANSCRIPTION_PROMPT_TEMPLATE if (TRANSCRIPTION_PROMPT_TEMPLATE is not None) else "",
    }

    if request.method == "GET":
        return render_template_string(INDEX_HTML, **ctx)

    if not creds_ok:
        ctx["msg"] = "Креды не настроены. Добавьте merchrules в ~/.search-checkup-creds.json"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    title = (request.form.get("title") or "").strip()
    if not title:
        ctx["msg"] = "Укажите название задачи (обязательное поле)."
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    task_fields = {
        "title": title,
        "description": (request.form.get("description") or "").strip(),
        "status": (request.form.get("status") or "").strip(),
        "priority": (request.form.get("priority") or "").strip(),
        "assignee": (request.form.get("assignee") or "").strip(),
        "team": (request.form.get("team") or "").strip(),
        "task_type": (request.form.get("task_type") or "").strip(),
        "product": (request.form.get("product") or "").strip(),
        "link": (request.form.get("link") or "").strip(),
        "due_date": (request.form.get("due_date") or "").strip(),
    }

    file_content = None
    if "site_ids_file" in request.files and request.files["site_ids_file"].filename:
        file_content = request.files["site_ids_file"].read()

    site_ids = parse_site_ids(request.form.get("site_ids", ""), file_content)
    if not site_ids:
        ctx["msg"] = "Укажите хотя бы один site_id (в поле или файлом)."
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    import requests
    http_session = requests.Session()
    r = http_session.post(f"{base_url}/backend-v2/auth/login", json={"username": login, "password": password}, timeout=30)
    if r.status_code != 200:
        ctx["msg"] = f"Ошибка авторизации Roadmap API: {r.status_code}\n{r.text[:500]}"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    # Сначала создаём одну задачу (лимит 5/час), затем копируем на остальные (лимит 10/мин)
    site_ids_list = list(site_ids)
    first_sid = site_ids_list[0]
    csv_body = build_task_csv_row(task_fields)
    csv_bytes = _csv_to_bytes(csv_body)
    import_url = f"{base_url}/backend-v2/import/tasks/csv"

    files_one = {"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv; charset=utf-8")}
    try:
        r_import = http_session.post(import_url, data={"site_id": str(first_sid)}, files=files_one, timeout=30)
    except Exception as e:
        ctx["msg"] = f"Ошибка при создании задачи: {e}"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    try:
        body_import = r_import.json()
    except Exception:
        ctx["msg"] = f"Ответ импорта (не JSON): {r_import.status_code}\n{r_import.text[:500]}"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    if r_import.status_code != 200:
        err_msg = body_import.get("message") or body_import.get("detail") or body_import.get("error") or r_import.text[:300]
        ctx["msg"] = f"Ошибка API создания {r_import.status_code}: {err_msg or 'нет текста'}"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    created_count = body_import.get("created", 0)
    if created_count is None or (created_count or 0) < 1:
        err_msg = body_import.get("detail") or body_import.get("message") or body_import.get("error") or str(body_import)[:200]
        ctx["msg"] = f"Задача не создана для {first_sid}: {err_msg or 'API вернул created: 0'}"
        ctx["msg_class"] = "err"
        return render_template_string(INDEX_HTML, **ctx)

    # Извлекаем task_id из ответа (разные варианты полей в API)
    task_id = body_import.get("task_id") or (body_import.get("task") or {}).get("id")
    if not task_id:
        task_id = (body_import.get("task_ids") or [None])[0]
    if not task_id and body_import.get("tasks"):
        task_id = (body_import.get("tasks") or [{}])[0].get("id")
    if not task_id and body_import.get("created_tasks"):
        task_id = (body_import.get("created_tasks") or [{}])[0].get("id")
    if isinstance(task_id, dict):
        task_id = task_id.get("id")

    # Если в ответе импорта нет task_id — запрашиваем список задач партнёра и ищем по названию (GET /backend-v2/roadmap)
    if not task_id and len(site_ids_list) > 1:
        list_url = f"{base_url}/backend-v2/roadmap"
        r_list = http_session.get(
            list_url,
            params={
                "site_id": str(first_sid),
                "page_size": 50,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            timeout=30,
        )
        if r_list.status_code == 200:
            try:
                body_list = r_list.json()
                tasks = body_list.get("tasks") or []
                want_title = (title or "").strip()
                for t in tasks:
                    if (t.get("title") or "").strip() == want_title:
                        task_id = t.get("id")
                        break
            except Exception:
                pass

    lines = [f"  {first_sid}: создана 1 задача (источник для копирования)"]
    total_created = 1
    if task_id and not (body_import.get("task_id") or body_import.get("task") or body_import.get("task_ids") or body_import.get("tasks")):
        lines.append("  (task_id получен из GET /roadmap по названию задачи)")

    if not task_id and len(site_ids_list) > 1:
        lines.append("\n(Ответ API создания не содержит task_id; поиск по списку задач по названию не дал результата.)")

    if len(site_ids_list) > 1 and task_id:
        # Копирование на остальные site_id (лимит 10 запросов/мин, до 100 site_id в одном запросе)
        remaining = [str(s) for s in site_ids_list[1:]]
        copy_base = COPY_API_BASE_URL.rstrip("/")
        session_copy = requests.Session()
        r_login = session_copy.post(
            f"{copy_base}/auth/login",
            json={"username": login, "password": password},
            timeout=30,
        )
        if r_login.status_code != 200:
            lines.append(f"\nКопирование отменено: ошибка входа в API копирования {r_login.status_code}")
            ctx["msg"] = f"Итого создано задач: {total_created}\n" + "\n".join(lines)
            ctx["msg_class"] = "ok"
            return render_template_string(INDEX_HTML, **ctx)

        r_copy = session_copy.post(
            f"{copy_base}/roadmap/{task_id}/copy",
            json={"site_ids": remaining[:100]},
            timeout=30,
        )
        try:
            body_copy = r_copy.json()
        except Exception:
            lines.append(f"\nКопирование: ответ не JSON — {r_copy.status_code} {r_copy.text[:200]}")
        else:
            if r_copy.status_code != 200:
                err_c = body_copy.get("message") or body_copy.get("detail") or body_copy.get("error") or r_copy.text[:200]
                lines.append(f"\nКопирование ошибка {r_copy.status_code}: {err_c}")
            else:
                copied = body_copy.get("copied") or []
                failed = body_copy.get("failed") or []
                total_created += len(copied)
                for c in copied:
                    sid = c.get("site_id") or (c.get("task") or {}).get("site_id")
                    if sid:
                        lines.append(f"  {sid}: скопировано")
                for f in failed:
                    sid = f.get("site_id", "?")
                    err = f.get("error", "?")
                    lines.append(f"  {sid}: ошибка копирования — {err}")
                if len(remaining) > 100:
                    lines.append(f"  (остальные {len(remaining) - 100} не отправлены — макс. 100 за запрос)")

    elif len(site_ids_list) > 1 and not task_id:
        lines.append("\nКопирование не выполнено: в ответе создания нет task_id (проверьте формат ответа API импорта).")
        lines.append("Остальные site_id можно добавить вручную или повторить создание для каждого (учти лимит 5 запросов/час).")

    ctx["msg"] = f"Итого создано задач: {total_created}\n" + "\n".join(lines)
    ctx["msg_class"] = "ok" if total_created > 0 else "err"
    return render_template_string(INDEX_HTML, **ctx)


if __name__ == "__main__":
    import os
    import sys
    import threading
    port = int(os.environ.get("PORT", 5051))
    url = "http://127.0.0.1:%s" % port
    print("Сервер: %s" % url, flush=True)
    print("Нажмите Enter в этом окне для остановки и освобождения порта.", flush=True)
    def run_server():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    try:
        if sys.stdin.isatty():
            input()
        else:
            # Нет TTY (фоновый запуск) — ждём indefinitely
            threading.Event().wait()
    except (EOFError, KeyboardInterrupt):
        pass
    os._exit(0)
