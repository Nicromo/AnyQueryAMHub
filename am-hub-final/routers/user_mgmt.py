"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging, secrets, string, csv, io

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote, ClientHistory,
)
from auth import decode_access_token, hash_password, verify_password, log_audit
from deps import require_user, require_admin, optional_user
from error_handlers import log_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

@router.post("/api/import/clients-csv")
async def api_import_clients_csv(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Импорт клиентов из CSV файла."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    import io, pandas as pd
    content_bytes = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content_bytes), dtype=str)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения CSV: {e}")

    created = updated = skipped = 0
    from sqlalchemy.orm.attributes import flag_modified
    for _, row in df.iterrows():
        name = str(row.get("name") or row.get("название") or row.get("client_name") or "").strip()
        if not name or name == "nan":
            skipped += 1
            continue
        client = db.query(Client).filter(Client.name == name).first()
        if client:
            for field, col in [("segment","segment"),("domain","domain"),("health_score","health_score")]:
                v = str(row.get(col) or "").strip()
                if v and v != "nan":
                    if field == "health_score":
                        try: setattr(client, field, float(v.replace("%","")))
                        except: pass
                    else: setattr(client, field, v)
            updated += 1
        else:
            seg = str(row.get("segment") or "").strip()
            domain = str(row.get("domain") or "").strip()
            client = Client(name=name, segment=seg or None, domain=domain or None,
                           manager_email=user.email)
            db.add(client)
            created += 1
    db.commit()
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped}


# ── Roadmap create ───────────────────────────────────────────────────────────


@router.post("/api/my-day/schedule")
async def api_my_day_schedule(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить расписание задач на день."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    data = await request.json()
    settings = user.settings or {}
    settings["my_day_schedule"] = data.get("schedule", [])
    settings["my_day_date"] = data.get("date")
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}




@router.get("/api/my-day/schedule")
async def api_get_my_day_schedule(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить расписание задач на день."""
    if not auth_token:
        return {"schedule": [], "date": None}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"schedule": [], "date": None}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"schedule": [], "date": None}
    settings = user.settings or {}
    return {"schedule": settings.get("my_day_schedule", []), "date": settings.get("my_day_date")}




@router.post("/api/profile/update")
async def api_profile_update(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Обновить имя/фамилию/telegram_id."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    data = await request.json()
    if "first_name" in data:
        user.first_name = data["first_name"].strip()
    if "last_name" in data:
        user.last_name = data["last_name"].strip()
    if "telegram_id" in data:
        tg = data["telegram_id"].strip()
        # Проверяем что такой TG ID не занят другим юзером
        if tg and db.query(User).filter(User.telegram_id == tg, User.id != user.id).first():
            return {"ok": False, "error": "Этот Telegram ID уже привязан к другому аккаунту"}
        user.telegram_id = tg or None
    db.commit()
    return {"ok": True}




@router.post("/api/profile/password")
async def api_change_password(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сменить пароль."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    data = await request.json()
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    confirm = data.get("confirm_password", "")

    if not new_pw or len(new_pw) < 8:
        return {"ok": False, "error": "Новый пароль должен быть не менее 8 символов"}
    if new_pw != confirm:
        return {"ok": False, "error": "Пароли не совпадают"}
    if user.hashed_password and not verify_password(current, user.hashed_password):
        return {"ok": False, "error": "Неверный текущий пароль"}

    user.hashed_password = hash_password(new_pw)
    db.commit()
    return {"ok": True}


# ── Profile save (from profile.html + onboarding wizard) ─────────────────────

@router.post("/api/profile/save")
async def api_profile_save(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить профиль: имя, Merchrules, Airtable, KTalk, TG, Groq."""
    if not auth_token: raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    data = await request.json()
    settings = dict(user.settings or {})

    if "display_name" in data:
        parts = data["display_name"].strip().split(None, 1)
        user.first_name = parts[0] if parts else user.first_name
        user.last_name  = parts[1] if len(parts) > 1 else (user.last_name or "")
    if "mr_login"    in data: settings["mr_login"]    = data["mr_login"].strip()
    if "mr_password" in data: settings["mr_password"] = data["mr_password"].strip()
    if "tg_notify_chat"  in data: settings["tg_notify_chat"]  = data["tg_notify_chat"].strip()
    if "airtable_token"  in data: settings["airtable_token"]  = data["airtable_token"].strip()
    if "ktalk_webhook"   in data: settings["ktalk_webhook"]   = data["ktalk_webhook"].strip()
    if "groq_api_key"    in data: settings["groq_api_key"]    = data["groq_api_key"].strip()

    from sqlalchemy.orm.attributes import flag_modified
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}


@router.post("/api/profile/test-mr")
async def api_profile_test_mr(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Проверить Merchrules credentials."""
    if not auth_token: return {"ok": False, "error": "Не авторизован"}
    data = await request.json()
    login = data.get("login", "").strip()
    password = data.get("password", "").strip()
    if not login or not password:
        return {"ok": False, "error": "Введи логин и пароль"}
    try:
        import urllib.request, json as _json
        payload = _json.dumps({"login": login, "password": password}).encode()
        for base_url in ["https://merchrules.any-platform.ru", "https://qa.merchrules.any-platform.ru"]:
            try:
                req = urllib.request.Request(
                    f"{base_url}/api/v1/auth/login",
                    data=payload, method="POST",
                    headers={"Content-Type": "application/json", "User-Agent": "AMHub/1.0"}
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    d = _json.loads(resp.read())
                    return {"ok": True, "email": d.get("email") or login}
            except Exception:
                continue
        return {"ok": False, "error": "Недоступен. Проверь логин/пароль или используй расширение Chrome"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


@router.post("/api/profile/test-ktalk")
async def api_profile_test_ktalk(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить KTalk webhook."""
    if not auth_token: return {"ok": False, "error": "Не авторизован"}
    data = await request.json()
    url = data.get("webhook_url", "").strip()
    if not url: return {"ok": False, "error": "URL не указан"}
    try:
        import urllib.request, json as _json
        body = _json.dumps({"text": "✅ AM Hub: тест подключения"}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


@router.post("/api/profile/test-groq")
async def api_profile_test_groq(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить Groq API ключ."""
    if not auth_token: return {"ok": False, "error": "Не авторизован"}
    data = await request.json()
    key = data.get("groq_api_key", "").strip()
    if not key: return {"ok": False, "error": "Ключ не указан"}
    try:
        import urllib.request, json as _json
        body = _json.dumps({
            "model": "llama3-8b-8192",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = _json.loads(resp.read())
            model = d.get("model", "llama3")
            return {"ok": True, "model": model}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        if e.code == 401: return {"ok": False, "error": "Неверный API ключ"}
        return {"ok": False, "error": f"HTTP {e.code}: {body[:80]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}




@router.post("/api/import/tasks-csv")
async def api_import_tasks_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт задач из CSV/Excel файла.

    Ожидаемые колонки:
      title / название / задача
      client / клиент / account
      status / статус
      priority / приоритет
      due_date / дедлайн / срок
      team / команда
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    content = await file.read()
    filename = file.filename or ""

    try:
        import pandas as pd, io
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, sep=None, engine="python")
                    break
                except Exception:
                    continue
            else:
                return {"error": "Не удалось прочитать файл"}
    except Exception as e:
        return {"error": f"Ошибка чтения файла: {e}"}

    df.columns = [str(c).strip().lower() for c in df.columns]

    def find_col(df, variants):
        for v in variants:
            for c in df.columns:
                if v in c:
                    return c
        return None

    col_title    = find_col(df, ["title", "название", "задача", "task", "name"])
    col_client   = find_col(df, ["client", "клиент", "account", "аккаунт", "site"])
    col_status   = find_col(df, ["status", "статус"])
    col_priority = find_col(df, ["priority", "приоритет"])
    col_due      = find_col(df, ["due", "дедлайн", "срок", "date"])
    col_team     = find_col(df, ["team", "команда"])
    col_mr_id    = find_col(df, ["merchrules_task", "task_id", "mr_id", "id"])

    if not col_title:
        return {"error": f"Не найдена колонка с названием задачи. Колонки: {list(df.columns)}"}

    STATUS_MAP = {
        "plan": "plan", "в работе": "in_progress", "in_progress": "in_progress",
        "review": "review", "done": "done", "готово": "done",
        "blocked": "blocked", "заблок": "blocked",
    }

    created = skipped = 0
    # Кешируем клиентов для поиска
    all_clients = {c.name.lower(): c for c in db.query(Client).all()}

    for idx, row in df.iterrows():
        title = str(row.get(col_title, "")).strip()
        if not title or title.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        # Ищем клиента
        client_id = None
        if col_client:
            client_name = str(row.get(col_client, "")).strip().lower()
            if client_name and client_name not in ("nan", ""):
                # Точное совпадение
                c = all_clients.get(client_name)
                if not c:
                    # Частичное совпадение
                    for cname, cobj in all_clients.items():
                        if client_name in cname or cname in client_name:
                            c = cobj
                            break
                if c:
                    client_id = c.id

        # Статус
        raw_status = str(row.get(col_status, "plan")).strip().lower() if col_status else "plan"
        status_val = STATUS_MAP.get(raw_status, "plan")

        # Дедлайн
        due_date = None
        if col_due:
            raw_due = str(row.get(col_due, "")).strip()
            if raw_due and raw_due not in ("nan", ""):
                for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
                    try:
                        due_date = datetime.strptime(raw_due[:10], fmt)
                        break
                    except Exception:
                        continue

        # Проверяем дубль по merchrules_task_id
        mr_id = str(row.get(col_mr_id, "")).strip() if col_mr_id else ""
        if mr_id and mr_id not in ("nan", ""):
            existing = db.query(Task).filter(Task.merchrules_task_id == mr_id).first()
            if existing:
                skipped += 1
                continue

        task = Task(
            client_id=client_id,
            title=title,
            status=status_val,
            priority=str(row.get(col_priority, "medium")).strip().lower() if col_priority else "medium",
            due_date=due_date,
            team=str(row.get(col_team, "")).strip() if col_team else None,
            source="import",
            merchrules_task_id=mr_id if mr_id not in ("nan", "") else None,
        )
        db.add(task)
        created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {"error": f"Ошибка сохранения: {e}"}

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "total_rows": len(df),
    }



@router.post("/api/internal-task")


@router.get("/api/internal-task")
async def api_internal_task(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Внутренние задачи менеджера (не привязаны к клиенту)."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    if request.method == "GET":
        tasks = db.query(Task).filter(
            Task.client_id.is_(None),
            Task.created_at >= datetime.utcnow() - timedelta(days=90)
        ).order_by(Task.created_at.desc()).limit(50).all()
        return {"tasks": [{"id":t.id,"title":t.title,"status":t.status,"priority":t.priority,
                           "due_date":t.due_date.isoformat() if t.due_date else None} for t in tasks]}

    body = await request.json()
    task = Task(
        client_id=None, title=body.get("title","Задача"),
        status=body.get("status","plan"), priority=body.get("priority","medium"),
        description=body.get("description",""),
        due_date=datetime.fromisoformat(body["due_date"]) if body.get("due_date") else None,
        created_at=datetime.utcnow(),
    )
    db.add(task); db.commit(); db.refresh(task)
    return {"ok": True, "id": task.id}


# ────────────────────────────────────────────────────────────────────────────
# ADMIN: управление пользователями
# ────────────────────────────────────────────────────────────────────────────


def _require_admin(auth_token: Optional[str], db: Session) -> Optional[User]:
    """Возвращает admin user или None (для редиректа)."""
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin" or not user.is_active:
        return None
    return user


def _gen_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Страница управления пользователями (только для admin)."""
    user = _require_admin(auth_token, db)
    if not user:
        return RedirectResponse(url="/dashboard?error=no_permission", status_code=303)

    users_list = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "users_admin.html",
        {"request": request, "user": user, "users": users_list},
    )


@router.post("/api/admin/users")
async def create_user_api(
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    role: str = Form("manager"),
    password: Optional[str] = Form(None),
    telegram_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"ok": False, "error": "Некорректный email"}, status_code=400)

    if role not in ("admin", "manager", "viewer"):
        role = "manager"

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        return JSONResponse({"ok": False, "error": "Email уже существует"}, status_code=400)

    tg = (telegram_id or "").strip() or None
    if tg and db.query(User).filter(User.telegram_id == tg).first():
        return JSONResponse({"ok": False, "error": "Этот Telegram ID уже занят"}, status_code=400)

    plain_pw = (password or "").strip()
    generated = False
    if not plain_pw:
        plain_pw = _gen_password()
        generated = True

    new_user = User(
        email=email,
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        role=role,
        telegram_id=tg,
        hashed_password=hash_password(plain_pw),
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    try:
        log_audit(db, user.id, "create", "user", new_user.id, None, {"email": email, "role": role})
    except Exception:
        pass

    return {
        "ok": True,
        "id": new_user.id,
        "email": new_user.email,
        "password": plain_pw if generated else None,
        "generated": generated,
    }


@router.post("/api/admin/users/import-csv")
async def import_users_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    content_bytes = await file.read()
    text_content = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            text_content = content_bytes.decode(enc)
            break
        except Exception:
            continue
    if text_content is None:
        raise HTTPException(status_code=400, detail="Не удалось декодировать CSV")

    try:
        # Авто-детект разделителя
        sample = text_content[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text_content), dialect=dialect)
        rows = list(reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения CSV: {e}")

    created = 0
    skipped = 0
    errors: List[str] = []
    created_list: List[dict] = []

    for idx, row in enumerate(rows, start=2):
        # нормализуем ключи
        nrow = { (k or "").strip().lower().lstrip("\ufeff"): (v or "").strip() for k, v in row.items() if k }
        email = nrow.get("email", "").lower()
        if not email or "@" not in email:
            skipped += 1
            errors.append(f"Строка {idx}: пустой или некорректный email")
            continue
        if db.query(User).filter(User.email == email).first():
            skipped += 1
            errors.append(f"Строка {idx}: email {email} уже существует")
            continue
        role = nrow.get("role", "manager") or "manager"
        if role not in ("admin", "manager", "viewer"):
            role = "manager"
        tg = nrow.get("telegram_id") or None
        if tg and db.query(User).filter(User.telegram_id == tg).first():
            tg = None  # не блокируем импорт, просто не ставим
        plain_pw = _gen_password()
        try:
            u = User(
                email=email,
                first_name=nrow.get("first_name") or None,
                last_name=nrow.get("last_name") or None,
                role=role,
                telegram_id=tg,
                hashed_password=hash_password(plain_pw),
                is_active=True,
            )
            db.add(u)
            db.flush()
            created += 1
            created_list.append({"email": email, "password": plain_pw})
        except Exception as e:
            db.rollback()
            skipped += 1
            errors.append(f"Строка {idx}: {e}")

    db.commit()
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "created_users": created_list,
    }


@router.post("/api/admin/users/{uid}/reset-password")
async def reset_password_api(
    uid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    target = db.query(User).filter(User.id == uid).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    new_pw = _gen_password()
    target.hashed_password = hash_password(new_pw)
    db.commit()

    try:
        log_audit(db, user.id, "reset_password", "user", target.id, None, None)
    except Exception:
        pass

    return {"ok": True, "password": new_pw, "email": target.email}


@router.post("/api/admin/users/{uid}/deactivate")
async def deactivate_user_api(
    uid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    if uid == user.id:
        return JSONResponse({"ok": False, "error": "Нельзя деактивировать себя"}, status_code=400)

    target = db.query(User).filter(User.id == uid).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target.is_active = False
    db.commit()

    try:
        log_audit(db, user.id, "deactivate", "user", target.id, None, None)
    except Exception:
        pass

    return {"ok": True}


@router.post("/api/admin/users/{uid}/activate")
async def activate_user_api(
    uid: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    target = db.query(User).filter(User.id == uid).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    target.is_active = True
    db.commit()
    return {"ok": True}


@router.post("/api/admin/users/{uid}/role")
async def change_role_api(
    uid: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    if role not in ("admin", "manager", "viewer"):
        return JSONResponse({"ok": False, "error": "Недопустимая роль"}, status_code=400)

    target = db.query(User).filter(User.id == uid).first()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    old_role = target.role
    target.role = role
    db.commit()

    try:
        log_audit(db, user.id, "change_role", "user", target.id, {"role": old_role}, {"role": role})
    except Exception:
        pass

    return {"ok": True, "role": role}


@router.get("/api/admin/users/export.csv")
async def export_users_csv(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_admin(auth_token, db)
    if not user:
        raise HTTPException(status_code=403, detail="Admin required")

    users_list = db.query(User).order_by(User.id.asc()).all()
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM for Excel
    writer = csv.writer(buf)
    writer.writerow(["id", "email", "first_name", "last_name", "role", "telegram_id", "is_active", "created_at"])
    for u in users_list:
        writer.writerow([
            u.id,
            u.email or "",
            u.first_name or "",
            u.last_name or "",
            u.role or "",
            u.telegram_id or "",
            "1" if u.is_active else "0",
            u.created_at.isoformat() if u.created_at else "",
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=users_export.csv"},
    )




