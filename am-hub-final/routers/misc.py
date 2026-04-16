"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Cookie, Form, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.get("/api/auto-tasks/rules")
async def api_auto_task_rules_list(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    from sqlalchemy import or_
    rules = db.query(AutoTaskRule).filter(
        or_(AutoTaskRule.user_id == user.id, AutoTaskRule.user_id.is_(None))
    ).order_by(AutoTaskRule.created_at.desc()).all()
    return {"rules": [{"id":r.id,"name":r.name,"trigger":r.trigger,"trigger_config":r.trigger_config,
                        "segment_filter":r.segment_filter,"task_title":r.task_title,
                        "task_description":r.task_description,"task_priority":r.task_priority,
                        "task_due_days":r.task_due_days,"task_type":r.task_type,
                        "is_active":r.is_active,"created_at":r.created_at.isoformat() if r.created_at else None}
                       for r in rules]}



@router.post("/api/auto-tasks/rules")
async def api_auto_task_rules_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    from models import AutoTaskRule
    rule = AutoTaskRule(user_id=user.id, **{k:v for k,v in body.items()
                         if k in ("name","trigger","trigger_config","segment_filter","task_title",
                                  "task_description","task_priority","task_due_days","task_type","is_active")})
    db.add(rule); db.commit(); db.refresh(rule)
    return {"ok": True, "id": rule.id}



@router.put("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_update(rule_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    from sqlalchemy.orm.attributes import flag_modified
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)
    body = await request.json()
    for k, v in body.items():
        if hasattr(rule, k): setattr(rule, k, v)
    db.commit()
    return {"ok": True}



@router.patch("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_patch(rule_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)
    body = await request.json()
    for k, v in body.items():
        if hasattr(rule, k): setattr(rule, k, v)
    db.commit()
    return {"ok": True}



@router.delete("/api/auto-tasks/rules/{rule_id}")
async def api_auto_task_rules_delete(rule_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if rule: db.delete(rule); db.commit()
    return {"ok": True}



@router.post("/api/auto-tasks/rules/{rule_id}/test")
async def api_auto_task_rules_test(rule_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Тестовый прогон правила — создаёт задачи для подходящих клиентов."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import AutoTaskRule
    rule = db.query(AutoTaskRule).filter(AutoTaskRule.id == rule_id).first()
    if not rule: raise HTTPException(status_code=404)

    q = db.query(Client)
    if user.role == "manager": q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    segs = rule.segment_filter or []
    if segs: clients = [c for c in clients if c.segment in segs]

    cfg = rule.trigger_config or {}
    triggered = []
    now = datetime.utcnow()

    for c in clients:
        match = False
        if rule.trigger == "health_drop":
            threshold = cfg.get("threshold", 50)
            match = (c.health_score or 0) < threshold
        elif rule.trigger == "days_no_contact":
            days = cfg.get("days", 30)
            last = c.last_meeting_date or c.last_checkup
            match = not last or (now - last).days >= days
        elif rule.trigger == "checkup_due":
            interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
            last = c.last_meeting_date or c.last_checkup
            match = not last or (now - last).days >= interval
        if match:
            triggered.append(c)

    created = 0
    for c in triggered[:10]:  # Лимит 10 за тест
        due = now + timedelta(days=rule.task_due_days or 3)
        task = Task(
            client_id=c.id, title=rule.task_title,
            description="[Автозадача: " + rule.name + "]\n" + (rule.task_description or ""),
            status="plan", priority=rule.task_priority or "medium",
            due_date=due, created_at=now,
        )
        db.add(task)
        created += 1

    db.commit()
    return {"ok": True, "triggered": len(triggered), "created": created}


# ── Followup templates ──────────────────────────────────────────────────────


@router.get("/api/followup/templates")
async def api_followup_templates_list(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    tmpls = db.query(FollowupTemplate).filter(FollowupTemplate.user_id == user.id).order_by(FollowupTemplate.created_at.desc()).all()
    return {"templates": [{"id":t.id,"name":t.name,"content":t.content,"category":t.category,"created_at":t.created_at.isoformat() if t.created_at else None} for t in tmpls]}



@router.post("/api/followup/templates")
async def api_followup_templates_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    t = FollowupTemplate(user_id=user.id, name=body["name"], content=body["content"], category=body.get("category","general"))
    db.add(t); db.commit(); db.refresh(t)
    return {"ok": True, "id": t.id}



@router.put("/api/followup/templates/{tmpl_id}")
async def api_followup_templates_update(tmpl_id: int, request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    t = db.query(FollowupTemplate).filter(FollowupTemplate.id == tmpl_id, FollowupTemplate.user_id == user.id).first()
    if not t: raise HTTPException(status_code=404)
    body = await request.json()
    for k in ("name","content","category"):
        if k in body: setattr(t, k, body[k])
    db.commit()
    return {"ok": True}



@router.delete("/api/followup/templates/{tmpl_id}")
async def api_followup_templates_delete(tmpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    t = db.query(FollowupTemplate).filter(FollowupTemplate.id == tmpl_id, FollowupTemplate.user_id == user.id).first()
    if t: db.delete(t); db.commit()
    return {"ok": True}



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

@router.post("/api/roadmap/create")
async def api_roadmap_create(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    task = Task(client_id=body.get("client_id"), title=body.get("title",""), 
                status="plan", priority=body.get("priority","medium"),
                created_at=datetime.utcnow())
    db.add(task); db.commit(); db.refresh(task)
    return {"ok": True, "id": task.id}

# ============================================================================
# MANAGER CABINET — личный кабинет менеджера

@router.get("/api/cabinet/my-clients")
async def api_cabinet_my_clients(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Клиенты текущего менеджера (назначены через assignment или manager_email)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    # Объединяем: manager_email ИЛИ user_client_assignment
    from sqlalchemy import or_
    assigned_ids = [a.client_id for a in
                    db.query(UserClientAssignment).filter(UserClientAssignment.user_id == user.id).all()]
    clients = db.query(Client).filter(
        or_(Client.manager_email == user.email, Client.id.in_(assigned_ids))
    ).order_by(Client.name).all()

    now = datetime.now()
    result = []
    for c in clients:
        meta = c.integration_metadata or {}
        digi = meta.get("diginetica", {})
        products = [p for p in ("sort", "autocomplete", "recommendations") if digi.get(p, {}).get("api_key")]
        if not products and meta.get("diginetica_api_key"):
            products = ["sort"]
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        days_since = (now - last).days if last else 999
        result.append({
            "id": c.id, "name": c.name, "segment": c.segment,
            "health_score": c.health_score,
            "domain": c.domain or meta.get("site_url", ""),
            "products": products,
            "has_api_keys": len(products) > 0,
            "days_since_checkup": days_since,
            "checkup_overdue": days_since > interval,
            "merchrules_id": c.merchrules_account_id,
            "last_meeting": c.last_meeting_date.isoformat() if c.last_meeting_date else None,
        })
    return {"clients": result, "total": len(result)}



@router.get("/api/cabinet/available-clients")
async def api_cabinet_available_clients(
    search: str = "",
    segment: str = "",
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Клиенты без менеджера или со свободными слотами — для добавления в свой кабинет."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from sqlalchemy import or_
    # Уже назначенные у этого менеджера
    my_ids = {a.client_id for a in
              db.query(UserClientAssignment).filter(UserClientAssignment.user_id == user.id).all()}
    my_emails = {user.email}

    q = db.query(Client)
    # Исключаем уже своих
    if my_ids:
        q = q.filter(~Client.id.in_(my_ids))
    q = q.filter(or_(Client.manager_email != user.email, Client.manager_email.is_(None)))

    if search:
        q = q.filter(Client.name.ilike(f"%{search}%"))
    if segment:
        q = q.filter(Client.segment == segment)

    clients = q.order_by(Client.name).limit(100).all()
    return {
        "clients": [
            {
                "id": c.id, "name": c.name, "segment": c.segment,
                "health_score": c.health_score,
                "manager_email": c.manager_email or "—",
            }
            for c in clients
        ]
    }



@router.post("/api/cabinet/assign/{client_id}")
async def api_cabinet_assign(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Добавить клиента в свой кабинет."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    # Проверяем нет ли уже
    existing = db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user.id,
        UserClientAssignment.client_id == client_id,
    ).first()
    if not existing:
        db.add(UserClientAssignment(user_id=user.id, client_id=client_id))
        # Устанавливаем manager_email если он пустой
        if not client.manager_email:
            client.manager_email = user.email
        db.commit()
    return {"ok": True}



@router.delete("/api/cabinet/assign/{client_id}")
async def api_cabinet_unassign(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Убрать клиента из своего кабинета."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user.id,
        UserClientAssignment.client_id == client_id,
    ).delete()
    db.commit()
    return {"ok": True}

# ============================================================================
# SEARCH QUALITY CHECKUP — API для расширения
# ============================================================================

def _checkup_auth(auth_token: Optional[str], db):
    """Общая авторизация для checkup endpoints."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user



@router.get("/api/cabinets/{cabinet_id}")
async def api_get_cabinet(
    cabinet_id: str,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Возвращает данные кабинета для расширения Search Quality Checkup.
    cabinet_id = client.id или client.merchrules_account_id
    """
    user = _checkup_auth(auth_token, db)

    # Ищем клиента по id или по merchrules_account_id
    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        raise HTTPException(status_code=404, detail=f"Кабинет {cabinet_id} не найден")

    meta = client.integration_metadata or {}
    api_key = (
        meta.get("diginetica_api_key")
        or meta.get("search_api_key")
        or meta.get("apiKey")
        or ""
    )
    site_url = client.domain or meta.get("site_url") or ""
    if site_url and not site_url.startswith("http"):
        site_url = "https://" + site_url

    digi = meta.get("diginetica", {})

    products = {}
    for product in ("sort", "autocomplete", "recommendations"):
        p = digi.get(product, {})
        if p.get("api_key"):
            products[product] = {
                "apiKey": p["api_key"],
                "url":    p.get("url", ""),
            }
    # Legacy fallback — если нет структурированных, используем diginetica_api_key как sort
    if not products and api_key:
        products["sort"] = {"apiKey": api_key, "url": "https://sort.diginetica.net/search"}

    return {
        "ok": True,
        "cabinetId": str(client.id),
        "clientName": client.name,
        # Основной ключ (Sort или первый доступный) — для обратной совместимости с расширением
        "apiKey": (products.get("sort") or next(iter(products.values()), {})).get("apiKey", ""),
        "siteUrl": site_url,
        "segment": client.segment,
        "healthScore": client.health_score,
        # Все продукты — расширение использует для выбора типа чекапа
        "products": products,
        "hasSort": "sort" in products,
        "hasAutocomplete": "autocomplete" in products,
        "hasRecommendations": "recommendations" in products,
    }



@router.get("/api/checkup/{cabinet_id}/queries")
async def api_checkup_queries(
    cabinet_id: str,
    type: str = "top",
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Возвращает список запросов для чекапа.
    type: top | random | zero | zeroquery
    Запросы берутся из integration_metadata.checkup_queries[type]
    или из сохранённых результатов предыдущих чекапов.
    """
    user = _checkup_auth(auth_token, db)

    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Кабинет не найден")

    meta = client.integration_metadata or {}
    checkup_queries = meta.get("checkup_queries", {})
    queries = checkup_queries.get(type, [])

    # Если нет сохранённых — возвращаем пустой список (расширение попросит ввести вручную)
    return {"ok": True, "queries": queries, "type": type, "client": client.name}



@router.get("/api/cabinets/{cabinet_id}/merch-rules")
async def api_merch_rules(
    cabinet_id: str,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Мерч-правила клиента из integration_metadata."""
    user = _checkup_auth(auth_token, db)

    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        return []

    meta = client.integration_metadata or {}
    return meta.get("merch_rules", [])



@router.post("/api/checkup/{cabinet_id}/results")
async def api_save_checkup_results(
    cabinet_id: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Сохраняет результаты чекапа из расширения.
    Расширение вызывает после завершения проверки.
    """
    user = _checkup_auth(auth_token, db)

    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(
            Client.merchrules_account_id == cabinet_id
        ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Кабинет не найден")

    body = await request.json()
    results = body.get("results", [])
    avg_score = (
        sum(r.get("manualScore") or r.get("autoScore", 0) for r in results) / len(results)
        if results else None
    )
    score_dist = {str(i): 0 for i in range(4)}
    for r in results:
        s = str(r.get("manualScore") or r.get("autoScore", 0))
        score_dist[s] = score_dist.get(s, 0) + 1

    from models import CheckupResult
    cr = CheckupResult(
        client_id=client.id,
        cabinet_id=cabinet_id,
        query_type=body.get("queryType", "top"),
        manager_name=body.get("managerName") or user.name,
        mode=body.get("mode"),
        total_queries=len(results),
        avg_score=avg_score,
        score_dist=score_dist,
        results=results,
    )
    db.add(cr)

    # Обновляем дату последнего чекапа у клиента
    client.last_checkup = datetime.utcnow()
    db.commit()

    logger.info(f"CheckupResult saved: client={client.name}, queries={len(results)}, avg={avg_score:.2f if avg_score else 'N/A'}")
    return {"ok": True, "id": cr.id, "avg_score": avg_score, "total": len(results)}



@router.get("/api/checkup/{cabinet_id}/history")
async def api_checkup_history(
    cabinet_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """История чекапов клиента."""
    user = _checkup_auth(auth_token, db)

    client_id = None
    if cabinet_id.isdigit():
        client_id = int(cabinet_id)
    else:
        c = db.query(Client).filter(Client.merchrules_account_id == cabinet_id).first()
        if c:
            client_id = c.id

    if not client_id:
        return {"results": []}

    from models import CheckupResult
    history = (
        db.query(CheckupResult)
        .filter(CheckupResult.client_id == client_id)
        .order_by(CheckupResult.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "results": [
            {
                "id": r.id,
                "query_type": r.query_type,
                "manager_name": r.manager_name,
                "mode": r.mode,
                "total_queries": r.total_queries,
                "avg_score": round(r.avg_score, 2) if r.avg_score else None,
                "score_dist": r.score_dist,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in history
        ]
    }



@router.get("/api/dashboard/actions")
async def api_dashboard_actions(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить карточки действий для дашборда."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    now = datetime.now()

    actions = []

    # 1. Фолоуапы pending
    pending_followups = db.query(Meeting).filter(
        Meeting.followup_status == "pending",
        Meeting.date < now,
    ).all()
    for m in pending_followups:
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({
            "type": "followup",
            "priority": "high",
            "meeting_id": m.id,
            "client_name": client.name if client else "—",
            "meeting_title": m.title or m.type,
            "meeting_date": m.date.isoformat() if m.date else None,
            "days_ago": (now - m.date).days if m.date else 0,
        })

    # 2. Prep до встречи
    upcoming = db.query(Meeting).filter(
        Meeting.date >= now,
        Meeting.date < now + timedelta(days=2),
    ).all()
    for m in upcoming:
        client = db.query(Client).filter(Client.id == m.client_id).first()
        actions.append({
            "type": "prep",
            "priority": "medium",
            "meeting_id": m.id,
            "client_name": client.name if client else "—",
            "meeting_title": m.title or m.type,
            "meeting_date": m.date.isoformat() if m.date else None,
            "hours_until": int((m.date - now).total_seconds() / 3600) if m.date else 0,
        })

    # 3. Chekups overdue
    overdue_checkups = db.query(CheckUp).filter(CheckUp.status == "overdue").all()
    for c in overdue_checkups:
        client = db.query(Client).filter(Client.id == c.client_id).first()
        actions.append({
            "type": "checkup",
            "priority": "high",
            "checkup_id": c.id,
            "client_name": client.name if client else "—",
            "checkup_type": c.type,
            "scheduled_date": c.scheduled_date.isoformat() if c.scheduled_date else None,
        })

    # 4. QBR overdue
    clients_qbr = db.query(Client).filter(
        Client.next_qbr_date != None,
        Client.next_qbr_date < now,
    ).all()
    for c in clients_qbr:
        actions.append({
            "type": "qbr",
            "priority": "high",
            "client_id": c.id,
            "client_name": c.name,
            "next_qbr_date": c.next_qbr_date.isoformat() if c.next_qbr_date else None,
        })

    return {"actions": actions, "total": len(actions)}


# ============================================================================
# ONBOARDING

@router.post("/api/onboarding/complete")
async def api_complete_onboarding(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить что онбординг пройден."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    settings = user.settings or {}
    settings["onboarding_complete"] = True
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}



# ============================================================================
# ADMIN PANEL
# ============================================================================

def _require_admin(auth_token, db):
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return user



@router.post("/api/admin/import/api-keys")
async def api_admin_import_api_keys(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт Diginetica API keys из Excel файла.
    Формат: client_id | client_name | diginetica_api_key | site_url | checkup_queries_top
    Доступно только администраторам.
    """
    _require_admin(auth_token, db)

    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Поддерживаются только .xlsx / .csv")

    content = await file.read()

    import io, pandas as pd
    try:
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content), dtype=str, skiprows=1)  # skiprows=1 пропускает описания
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла: {e}")

    df.columns = [c.strip().lower() for c in df.columns]
    if "client_id" not in df.columns:
        raise HTTPException(status_code=400, detail="Нет колонки client_id")

    updated, skipped, errors = 0, 0, []
    from sqlalchemy.orm.attributes import flag_modified

    for _, row in df.iterrows():
        raw_id = row.get("client_id", "")
        if not raw_id or str(raw_id).strip() in ("", "nan", "None"):
            skipped += 1
            continue

        try:
            client_id = int(float(str(raw_id).strip()))
        except ValueError:
            errors.append(f"Неверный client_id: {raw_id!r}")
            continue

        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            errors.append(f"Клиент #{client_id} не найден")
            continue

        meta = dict(client.integration_metadata or {})

        # Diginetica продукты — Sort / Autocomplete / Recommendations
        digi = meta.get("diginetica", {})

        def _val(key):
            v = str(row.get(key, "") or "").strip()
            return "" if v in ("", "nan", "None") else v

        # Sort
        if _val("sort_api_key"):
            digi.setdefault("sort", {})["api_key"] = _val("sort_api_key")
        if _val("sort_url"):
            digi.setdefault("sort", {})["url"] = _val("sort_url")
        # Autocomplete
        if _val("auto_api_key"):
            digi.setdefault("autocomplete", {})["api_key"] = _val("auto_api_key")
        if _val("auto_url"):
            digi.setdefault("autocomplete", {})["url"] = _val("auto_url")
        # Recommendations
        if _val("rec_api_key"):
            digi.setdefault("recommendations", {})["api_key"] = _val("rec_api_key")
        if _val("rec_url"):
            digi.setdefault("recommendations", {})["url"] = _val("rec_url")
        # Legacy single key
        if _val("diginetica_api_key"):
            meta["diginetica_api_key"] = _val("diginetica_api_key")
            digi.setdefault("sort", {})["api_key"] = _val("diginetica_api_key")

        if digi:
            meta["diginetica"] = digi

        # site_url
        site_url = _val("site_url")
        if site_url:
            if not site_url.startswith("http"):
                site_url = "https://" + site_url
            meta["site_url"] = site_url

        # Запросы для чекапа (top)
        queries_raw = _val("checkup_queries_top")
        if queries_raw:
            queries = [q.strip() for q in queries_raw.split(",") if q.strip()]
            cq = meta.get("checkup_queries", {})
            cq["top"] = queries
            meta["checkup_queries"] = cq

        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
        updated += 1

    db.commit()
    logger.info(f"Admin import API keys: updated={updated}, skipped={skipped}, errors={len(errors)}")
    return {"ok": True, "updated": updated, "skipped": skipped, "errors": errors}



@router.get("/api/admin/clients/api-keys")
async def api_admin_list_api_keys(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Список всех клиентов с их API keys (только admin)."""
    _require_admin(auth_token, db)
    clients = db.query(Client).order_by(Client.name).all()
    return {
        "clients": [
            {
                "id": c.id,
                "name": c.name,
                "segment": c.segment,
                "has_api_key": bool((c.integration_metadata or {}).get("diginetica_api_key")),
                "api_key": (c.integration_metadata or {}).get("diginetica_api_key", ""),
                "site_url": c.domain or (c.integration_metadata or {}).get("site_url", ""),
                "has_queries": bool((c.integration_metadata or {}).get("checkup_queries", {}).get("top")),
            }
            for c in clients
        ]
    }



@router.patch("/api/admin/clients/{client_id}/api-key")
async def api_admin_set_api_key(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Установить/обновить API key конкретного клиента (только admin)."""
    _require_admin(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    body = await request.json()
    from sqlalchemy.orm.attributes import flag_modified
    meta = dict(client.integration_metadata or {})

    if "diginetica_api_key" in body:
        meta["diginetica_api_key"] = body["diginetica_api_key"]
    if "site_url" in body:
        meta["site_url"] = body["site_url"]
    if "checkup_queries" in body:
        meta["checkup_queries"] = body["checkup_queries"]

    client.integration_metadata = meta
    flag_modified(client, "integration_metadata")
    db.commit()
    return {"ok": True}



@router.post("/api/admin/reset-data")
async def api_reset_data(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить все тестовые данные (только для админа)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403)

    # Удаляем в правильном порядке (из-за FK)
    db.query(Task).delete()
    db.query(Meeting).delete()
    db.query(CheckUp).delete()
    db.query(QBR).delete()
    db.query(AccountPlan).delete()
    db.query(Client).delete()
    db.commit()
    return {"ok": True, "message": "Все данные очищены"}



@router.get("/api/onboarding/status")
async def api_onboarding_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Проверить, пройден ли онбординг."""
    if not auth_token:
        return {"onboarding_complete": True}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"onboarding_complete": True}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"onboarding_complete": True}
    settings = user.settings or {}
    return {"onboarding_complete": settings.get("onboarding_complete", False)}


# ============================================================================
# GLOBAL SEARCH

@router.get("/api/search")
async def api_global_search(
    q: str = Query("", min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Глобальный поиск по клиентам, задачам, встречам, заметкам."""
    if not auth_token:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}

    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"clients": [], "tasks": [], "meetings": [], "notes": []}

    search_pattern = f"%{q}%"

    # Клиенты
    c_q = db.query(Client)
    if user.role == "manager":
        c_q = c_q.filter(Client.manager_email == user.email)
    clients = c_q.filter(
        Client.name.ilike(search_pattern) |
        (Client.segment is not None and Client.segment.ilike(search_pattern)),
    ).limit(limit).all()

    # Задачи
    task_query = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_query = task_query.filter(Client.manager_email == user.email)
    tasks = task_query.filter(
        Task.title.ilike(search_pattern) |
        (Task.description is not None and Task.description.ilike(search_pattern)),
    ).limit(limit).all()

    # Встречи
    meeting_query = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        meeting_query = meeting_query.filter(Client.manager_email == user.email)
    meetings = meeting_query.filter(
        (Meeting.title is not None and Meeting.title.ilike(search_pattern)) |
        (Meeting.type is not None and Meeting.type.ilike(search_pattern)),
    ).order_by(Meeting.date.desc()).limit(limit).all()

    # Заметки
    note_query = db.query(ClientNote).join(Client, ClientNote.client_id == Client.id, isouter=True)
    if user.role == "manager":
        note_query = note_query.filter(Client.manager_email == user.email)
    notes = note_query.filter(ClientNote.content.ilike(search_pattern)).order_by(
        ClientNote.is_pinned.desc(), ClientNote.updated_at.desc()
    ).limit(limit).all()

    return {
        "clients": [{"id": c.id, "name": c.name, "segment": c.segment, "url": f"/client/{c.id}", "type": "client"} for c in clients],
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "client_name": t.client.name if t.client else "—", "url": f"/client/{t.client_id}", "type": "task"} for t in tasks],
        "meetings": [{"id": m.id, "title": m.title or m.type, "date": m.date.isoformat() if m.date else None, "client_name": m.client.name if m.client else "—", "url": f"/client/{m.client_id}", "type": "meeting"} for m in meetings],
        "notes": [{"id": n.id, "content": n.content[:100] + "..." if len(n.content) > 100 else n.content, "client_name": n.client.name if n.client else "—", "url": f"/client/{n.client_id}", "type": "note", "pinned": n.is_pinned} for n in notes],
    }


# ============================================================================
# CLIENT NOTES API

@router.get("/api/kanban")
async def api_kanban(
    client_id: Optional[int] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Получить задачи в формате канбан (группировка по статусам)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if client_id:
        q = q.filter(Task.client_id == client_id)
    tasks = q.order_by(Task.due_date.asc()).all()

    columns = {"plan": [], "in_progress": [], "review": [], "done": [], "blocked": []}
    for t in tasks:
        status = t.status or "plan"
        if status not in columns:
            columns["plan"].append(t)
        else:
            columns[status].append(t)

    def task_dict(t):
        return {
            "id": t.id, "title": t.title, "priority": t.priority, "status": t.status or "plan",
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "client_name": t.client.name if t.client else "—",
            "client_id": t.client_id, "team": t.team,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }

    # Возвращаем оба формата — columns (для client_detail) и плоский (для kanban страницы)
    columns_list = [
        {"id": col, "tasks": [task_dict(t) for t in tlist]}
        for col, tlist in columns.items()
    ]
    return {
        "columns": columns_list,
        **{col: [task_dict(t) for t in tlist] for col, tlist in columns.items()}
    }



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



@router.get("/api/checkup/results/all")
async def api_checkup_results_all(
    query_type: str = "",
    limit: int = 50,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Все результаты чекапов менеджера (для страницы /checkups)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    from models import CheckupResult
    q = (
        db.query(CheckupResult, Client)
        .join(Client, CheckupResult.client_id == Client.id)
        .order_by(CheckupResult.created_at.desc())
    )
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if query_type:
        q = q.filter(CheckupResult.query_type == query_type)
    rows = q.limit(limit).all()

    return {
        "results": [
            {
                "id": r.id,
                "client_id": r.client_id,
                "client_name": c.name,
                "cabinet_id": r.cabinet_id,
                "query_type": r.query_type,
                "manager_name": r.manager_name,
                "mode": r.mode,
                "total_queries": r.total_queries,
                "avg_score": round(r.avg_score, 2) if r.avg_score is not None else None,
                "score_dist": r.score_dist,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r, c in rows
        ]
    }




@router.get("/api/checkups")
async def api_checkups(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить список чекапов по сегментам с дедлайнами."""
    if not auth_token:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"overdue": [], "due_soon": [], "upcoming": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"overdue": [], "due_soon": [], "upcoming": []}

    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    now = datetime.now()
    overdue, due_soon, upcoming = [], [], []

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            days_until = interval - days_since
        else:
            days_since = 999
            days_until = -30

        info = {"id": c.id, "name": c.name, "segment": c.segment, "days_since": days_since, "days_until": days_until, "interval": interval, "last_date": last.isoformat() if last else None}

        if days_until < 0:
            overdue.append(info)
        elif days_until <= 14:
            due_soon.append(info)
        elif days_until <= 30:
            upcoming.append(info)

    overdue.sort(key=lambda x: x["days_until"])
    due_soon.sort(key=lambda x: x["days_until"])
    upcoming.sort(key=lambda x: x["days_until"])

    return {"overdue": overdue, "due_soon": due_soon, "upcoming": upcoming}



@router.post("/api/checkups/assign")
async def api_assign_checkup(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Назначить чекап клиенту (создать встречу)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    data = await request.json()
    client_id = data.get("client_id")
    date_str = data.get("date")
    if not client_id:
        raise HTTPException(status_code=400)
    meeting_date = datetime.fromisoformat(date_str) if date_str else datetime.now()
    meeting = Meeting(client_id=client_id, date=meeting_date, type="checkup", source="internal", title="Чекап")
    db.add(meeting)
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.last_meeting_date = meeting_date
        client.needs_checkup = False
    db.commit()

    # Автозапись в Google Sheets
    if client:
        try:
            from sheets import write_checkup_status
            await write_checkup_status(
                client_name=client.name,
                status="Запланирован",
                last_date=meeting_date.strftime("%d.%m.%Y"),
            )
        except Exception as e:
            logger.debug(f"Sheets write-back skipped: {e}")

    return {"ok": True, "meeting_id": meeting.id}


# ============================================================================
# FOLLOWUP TEMPLATES

@router.get("/api/followup-templates")
async def api_get_followup_templates(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить шаблоны фолоуапов пользователя."""
    if not auth_token:
        return {"templates": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"templates": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"templates": []}
    templates_list = db.query(FollowupTemplate).filter(FollowupTemplate.user_id == user.id).order_by(FollowupTemplate.name).all()
    return {"templates": [{"id": t.id, "name": t.name, "content": t.content, "category": t.category} for t in templates_list]}



@router.post("/api/followup-templates")
async def api_create_followup_template(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Создать шаблон фолоуапа."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    tpl = FollowupTemplate(user_id=user.id, name=data.get("name", ""), content=data.get("content", ""), category=data.get("category", "general"))
    db.add(tpl)
    db.commit()
    return {"ok": True, "id": tpl.id}



@router.delete("/api/followup-templates/{tpl_id}")
async def api_delete_followup_template(tpl_id: int, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Удалить шаблон."""
    if not auth_token:
        raise HTTPException(status_code=401)
    tpl = db.query(FollowupTemplate).filter(FollowupTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(status_code=404)
    db.delete(tpl)
    db.commit()
    return {"ok": True}


# ============================================================================
# CALENDAR

@router.get("/api/calendar/events")
async def api_calendar_events(start: str = "", end: str = "", db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить события для календаря."""
    if not auth_token:
        return {"events": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"events": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"events": []}

    q = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    if start and end:
        q = q.filter(Meeting.date >= datetime.fromisoformat(start), Meeting.date <= datetime.fromisoformat(end))
    meetings = q.order_by(Meeting.date).all()

    events = []
    for m in meetings:
        color = {"checkup": "#22c55e", "qbr": "#6366f1", "kickoff": "#f97316", "sync": "#3b82f6"}.get(m.type, "#64748b")
        events.append({
            "id": m.id,
            "title": f"{m.client.name + ': ' if m.client else ''}{m.title or m.type}",
            "start": m.date.isoformat() if m.date else None,
            "color": color,
            "url": f"/client/{m.client_id}",
            "type": m.type,
        })

    # Также добавляем дедлайны задач
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    task_q = task_q.filter(Task.due_date != None, Task.status != "done")
    if start and end:
        task_q = task_q.filter(Task.due_date >= datetime.fromisoformat(start), Task.due_date <= datetime.fromisoformat(end))
    tasks = task_q.all()

    for t in tasks:
        events.append({
            "id": f"task-{t.id}",
            "title": f"⏰ {t.title}",
            "start": t.due_date.isoformat() if t.due_date else None,
            "color": "#ef4444",
            "url": f"/client/{t.client_id}",
            "type": "task",
        })

    return {"events": events}


# ============================================================================
# PROFILE

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



# ============================================================================
# SYNC STATUS

@router.get("/api/diagnostics/outbound-ip")
async def api_outbound_ip(auth_token: Optional[str] = Cookie(None)):
    """
    Возвращает внешний IP Railway-сервера.
    Этот IP нужно добавить в whitelist Merchrules.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Используем несколько сервисов для надёжности
            for url in ["https://api.ipify.org?format=json", "https://ifconfig.me/ip"]:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        text = resp.text.strip()
                        ip = resp.json().get("ip", text) if "json" in url else text
                        return {"ip": ip, "note": "Добавьте этот IP в whitelist Merchrules"}
                except Exception:
                    continue
    except Exception as e:
        return {"error": str(e)}
    return {"error": "Не удалось определить IP"}



@router.post("/api/diagnostics/merchrules-auth")
async def api_diag_merchrules_auth(
    request: Request,
    auth_token: Optional[str] = Cookie(None),
):
    """
    Диагностика авторизации Merchrules.
    Показывает точный HTTP-статус и ответ для каждой попытки.
    """
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)

    body = await request.json()
    login = body.get("login", "")
    password = body.get("password", "")
    if not login or not password:
        return {"error": "Нужны login и password"}

    import httpx
    results = []
    urls = list(dict.fromkeys([
        _env("MERCHRULES_API_URL", "https://merchrules.any-platform.ru"),
        "https://merchrules.any-platform.ru",
        "https://merchrules-qa.any-platform.ru",
    ]))
    fields = ["email", "login", "username"]

    async with httpx.AsyncClient(timeout=15) as hx:
        for url in urls:
            for field in fields:
                try:
                    resp = await hx.post(
                        f"{url}/backend-v2/auth/login",
                        json={field: login, "password": password},
                        timeout=10,
                    )
                    body_text = resp.text[:300]
                    has_token = False
                    if resp.status_code == 200:
                        try:
                            j = resp.json()
                            has_token = bool(j.get("token") or j.get("access_token") or j.get("accessToken"))
                        except Exception:
                            pass
                    results.append({
                        "url": url,
                        "field": field,
                        "status": resp.status_code,
                        "has_token": has_token,
                        "response": body_text,
                    })
                    # Нашли рабочий — дальше не пробуем
                    if resp.status_code == 200 and has_token:
                        return {"ok": True, "working": results[-1], "all": results}
                except Exception as e:
                    results.append({
                        "url": url,
                        "field": field,
                        "status": "error",
                        "error": str(e),
                    })

    return {"ok": False, "all": results}



async def api_import_clients_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """
    Импорт клиентов из CSV/Excel файла.

    Ожидаемые колонки (гибко — ищет по ключевым словам):
      name / название / клиент
      segment / сегмент
      manager_email / менеджер
      site_id / site_ids / merchrules_id
      health_score
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

    # Парсим файл
    try:
        import pandas as pd, io
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            # Пробуем разные кодировки и разделители
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc, sep=None, engine="python")
                    break
                except Exception:
                    continue
            else:
                return {"error": "Не удалось прочитать файл. Поддерживаются CSV и XLSX."}
    except Exception as e:
        return {"error": f"Ошибка чтения файла: {e}"}

    # Нормализуем названия колонок
    df.columns = [str(c).strip().lower() for c in df.columns]

    def find_col(df, variants):
        for v in variants:
            for c in df.columns:
                if v in c:
                    return c
        return None

    col_name    = find_col(df, ["name", "название", "клиент", "company", "account"])
    col_segment = find_col(df, ["segment", "сегмент", "тип"])
    col_manager = find_col(df, ["manager", "менеджер", "email"])
    col_site    = find_col(df, ["site_id", "site", "merchrules", "account_id"])
    col_health  = find_col(df, ["health", "score", "хелс"])

    if not col_name:
        return {"error": f"Не найдена колонка с именем клиента. Колонки в файле: {list(df.columns)}"}

    created = updated = skipped = 0
    errors = []

    for idx, row in df.iterrows():
        name = str(row.get(col_name, "")).strip()
        if not name or name.lower() in ("nan", "none", ""):
            skipped += 1
            continue

        segment   = str(row.get(col_segment, "")).strip() if col_segment else ""
        manager   = str(row.get(col_manager, "")).strip() if col_manager else user.email
        site_id   = str(row.get(col_site, "")).strip() if col_site else ""
        health    = None
        if col_health:
            try:
                health = float(str(row.get(col_health, "")).replace(",", ".").replace("%", ""))
                if health > 1:
                    health = health / 100
            except Exception:
                pass

        # Ищем существующего клиента
        existing = None
        if site_id and site_id not in ("nan", ""):
            existing = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
        if not existing:
            existing = db.query(Client).filter(Client.name == name).first()

        if existing:
            # Обновляем только непустые поля
            if segment and segment not in ("nan", ""):
                existing.segment = segment
            if manager and manager not in ("nan", "") and "@" in manager:
                existing.manager_email = manager
            if site_id and site_id not in ("nan", ""):
                existing.merchrules_account_id = site_id
            if health is not None:
                existing.health_score = health
            updated += 1
        else:
            c = Client(
                name=name,
                segment=segment if segment not in ("nan", "") else None,
                manager_email=manager if "@" in manager else user.email,
                merchrules_account_id=site_id if site_id not in ("nan", "") else None,
                health_score=health,
            )
            db.add(c)
            created += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {"error": f"Ошибка сохранения: {e}"}

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_rows": len(df),
        "columns_detected": {
            "name": col_name, "segment": col_segment,
            "manager": col_manager, "site_id": col_site,
        }
    }



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


# ============================================================================
# MEETING SLOTS

@router.get("/api/notifications")
async def api_notifications(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить уведомления: пора написать, просрочки и т.д."""
    if not auth_token:
        return {"notifications": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notifications": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"notifications": []}

    # Кеш 120 сек — polling каждые 60 сек от 18 менеджеров = экономим ~540 DB запросов/мин
    ck = f"notif:{user.id}"
    cached = cache_get(ck)
    if cached:
        return cached

    notifications = []
    now = datetime.now()

    # Клиенты без активности
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()

    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last:
            days_since = (now - last).days
            if days_since > interval:
                notifications.append({
                    "type": "overdue_checkup",
                    "priority": "high",
                    "message": f"Пора написать: {c.name} (последний контакт {days_since} дн. назад)",
                    "client_id": c.id,
                    "client_name": c.name,
                })
            elif days_since > interval - 14:
                notifications.append({
                    "type": "checkup_soon",
                    "priority": "medium",
                    "message": f"Скоро чекап: {c.name} (через {interval - days_since} дн.)",
                    "client_id": c.id,
                    "client_name": c.name,
                })

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        notifications.append({
            "type": "blocked_task",
            "priority": "high",
            "message": f"Заблокирована задача: {t.title} ({t.client.name if t.client else ''})",
            "client_id": t.client_id,
        })

    return {"notifications": notifications}


# ============================================================================
# FOCUS MODE (CSS toggle — client detail page with sidebar hidden)

@router.post("/api/voice-notes")
async def api_create_voice_note(
    request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)
):
    """Создать голосовую заметку (текстовую транскрипцию)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    vn = VoiceNote(
        client_id=data.get("client_id"),
        meeting_id=data.get("meeting_id"),
        user_id=user.id,
        transcription=data.get("text", ""),
        duration_seconds=data.get("duration", 0),
    )
    db.add(vn)
    # Авто-создание задачи из заметки
    if data.get("create_task"):
        db.add(Task(
            client_id=data.get("client_id"),
            title=f"🎤 {data.get('text', '')[:80]}",
            description=data.get("text", ""),
            status="plan",
            priority="medium",
            source="voice_note",
        ))
    db.commit()
    return {"ok": True, "id": vn.id}


# ============================================================================
# PERSONAL INBOX

@router.get("/api/inbox")
async def api_inbox(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить сообщения Inbox."""
    if not auth_token:
        return {"items": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"items": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    items = []
    now = datetime.now()

    # Новые уведомления
    notifs = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).order_by(Notification.created_at.desc()).limit(20).all()
    for n in notifs:
        items.append({"type": "notification", "title": n.title, "message": n.message, "date": n.created_at.isoformat() if n.created_at else None, "priority": n.type})

    # Просроченные чекапы
    q = db.query(Client)
    if user.role == "manager":
        q = q.filter(Client.manager_email == user.email)
    clients = q.all()
    for c in clients:
        interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
        last = c.last_meeting_date or c.last_checkup
        if last and (now - last).days > interval:
            items.append({"type": "overdue", "title": f"Просрочен чекап: {c.name}", "message": f"Последний контакт {(now-last).days} дн. назад (норма: {interval})", "date": last.isoformat(), "priority": "high", "client_id": c.id})

    # Blocked tasks
    task_q = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True)
    if user.role == "manager":
        task_q = task_q.filter(Client.manager_email == user.email)
    blocked = task_q.filter(Task.status == "blocked").all()
    for t in blocked:
        items.append({"type": "blocked", "title": f"Заблокирована: {t.title}", "message": t.client.name if t.client else "", "priority": "high", "client_id": t.client_id})

    items.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {"items": items[:50]}



@router.post("/api/inbox/mark-read")
async def api_inbox_mark_read(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Отметить уведомления прочитанными."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False).update({"is_read": True})
    db.commit()
    return {"ok": True}



@router.get("/api/inbox/items")
async def api_inbox_items(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Алиас /api/inbox для совместимости с base.html."""
    return await api_inbox(db=db, auth_token=auth_token)


# ============================================================================
# CHURN PREDICTION

@router.get("/api/voice-notes")
async def api_get_voice_notes(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить голосовые заметки пользователя."""
    if not auth_token:
        return {"notes": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"notes": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    notes = db.query(VoiceNote).filter(VoiceNote.user_id == user.id).order_by(VoiceNote.created_at.desc()).limit(50).all()
    return {"notes": [{"id": n.id, "text": n.transcription, "duration": n.duration_seconds, "client_id": n.client_id, "created_at": n.created_at.isoformat() if n.created_at else None} for n in notes]}


# ============================================================================
# PWA ICONS (SVG placeholder)

@router.post("/api/drafts")
async def api_save_draft(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Сохранить черновик (фолоуап, заметка) для офлайн-синхронизации."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    data = await request.json()
    settings = user.settings or {}
    drafts = settings.get("drafts", [])
    drafts.append({**data, "saved_at": datetime.utcnow().isoformat(), "user_id": user.id})
    settings["drafts"] = drafts[-50:]  # keep last 50
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}



@router.get("/api/drafts")
async def api_get_drafts(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Получить черновики."""
    if not auth_token:
        return {"drafts": []}
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return {"drafts": []}
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        return {"drafts": []}
    settings = user.settings or {}
    return {"drafts": settings.get("drafts", [])}


# ============================================================================
# TASK MODAL API (bulk edit)

@router.get("/api/manager/kpi")
async def api_manager_kpi(
    period_days: int = 30,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """KPI менеджера за период: задачи, встречи, фолоуапы, чекапы."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    since = datetime.utcnow() - timedelta(days=period_days)
    email = user.email

    # Задачи
    tasks_closed = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.status == "done",
        Task.confirmed_at >= since,
    ).count()

    tasks_created = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.created_at >= since,
    ).count()

    tasks_overdue = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Task.due_date < datetime.utcnow(),
        Task.status.in_(["plan", "in_progress"]),
    ).count()

    # Встречи
    meetings_held = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.date >= since,
        Meeting.date <= datetime.utcnow(),
    ).count()

    # Фолоуапы отправлены
    followups_sent = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.followup_status == "sent",
        Meeting.followup_sent_at >= since,
    ).count()

    followups_pending = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
        Client.manager_email == email,
        Meeting.followup_status == "pending",
        Meeting.date < datetime.utcnow(),
    ).count()

    # Клиенты
    total_clients = db.query(Client).filter(Client.manager_email == email).count()

    clients_no_contact = db.query(Client).filter(
        Client.manager_email == email,
        Client.last_meeting_date < datetime.utcnow() - timedelta(days=60),
    ).count()

    # Средний health score
    from sqlalchemy import func
    avg_health = db.query(func.avg(Client.health_score)).filter(
        Client.manager_email == email,
        Client.health_score != None,
    ).scalar() or 0

    return {
        "period_days": period_days,
        "manager": user.email,
        "tasks": {
            "closed": tasks_closed,
            "created": tasks_created,
            "overdue": tasks_overdue,
            "close_rate": round(tasks_closed / max(tasks_created, 1) * 100, 1),
        },
        "meetings": {
            "held": meetings_held,
            "followups_sent": followups_sent,
            "followups_pending": followups_pending,
            "followup_rate": round(followups_sent / max(meetings_held, 1) * 100, 1),
        },
        "clients": {
            "total": total_clients,
            "no_contact_60d": clients_no_contact,
            "avg_health_score": round(float(avg_health) * 100, 1),
        },
    }



@router.post("/api/checklist/init")

@router.post("/api/checklist/add")

@router.post("/api/checklist/clear")
async def api_checklist(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Чеклист встречи — хранится в user.settings."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    path = str(request.url.path)
    body = {}
    try: body = await request.json()
    except: pass

    from sqlalchemy.orm.attributes import flag_modified
    settings = dict(user.settings or {})

    if "init" in path:
        settings["checklist"] = [
            {"id": 1, "text": "Приветствие и цели встречи", "done": False},
            {"id": 2, "text": "Статус открытых задач", "done": False},
            {"id": 3, "text": "Метрики и показатели", "done": False},
            {"id": 4, "text": "Планы и следующие шаги", "done": False},
            {"id": 5, "text": "Фолоуап назначен", "done": False},
        ]
    elif "add" in path:
        cl = settings.get("checklist", [])
        new_id = max((i["id"] for i in cl), default=0) + 1
        cl.append({"id": new_id, "text": body.get("text", ""), "done": False})
        settings["checklist"] = cl
    elif "clear" in path:
        settings["checklist"] = []

    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True, "checklist": settings.get("checklist", [])}



@router.get("/api/checklist")
async def api_checklist_get(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    settings = user.settings or {}
    return {"checklist": settings.get("checklist", [])}



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



@router.post("/api/metrics/upload")
async def api_metrics_upload(
    file: UploadFile,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Загрузка метрик Top-50 (CSV/Excel)."""
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    import io, pandas as pd
    content_bytes = await file.read()
    try:
        if file.filename and file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content_bytes), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(content_bytes), dtype=str)
        df.columns = [c.strip().lower().replace(" ","_") for c in df.columns]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка файла: {e}")

    updated = 0
    from sqlalchemy.orm.attributes import flag_modified
    for _, row in df.iterrows():
        name = str(row.get("name") or row.get("название") or row.get("client") or "").strip()
        if not name or name == "nan": continue
        client = db.query(Client).filter(Client.name.ilike(f"%{name}%")).first()
        if not client: continue
        meta = dict(client.integration_metadata or {})
        for col in df.columns:
            v = str(row.get(col) or "").strip()
            if v and v != "nan" and col not in ("name","название","client"):
                meta[f"metric_{col}"] = v
        client.integration_metadata = meta
        flag_modified(client, "integration_metadata")
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated}


# ============================================================================
# AI CHAT

@router.get("/api/telegram/status")
async def api_tg_status(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    return {"connected": bool(sub and sub.chat_id), "chat_id": sub.chat_id if sub else None,
            "settings": {k: getattr(sub, k) for k in ("notify_overdue","notify_health_drop","notify_tasks","notify_daily")} if sub else {}}



@router.post("/api/telegram/connect")
async def api_tg_connect(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)

    body = await request.json()
    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id: return {"ok": False, "error": "chat_id обязателен"}

    # Проверяем что можем отправить сообщение
    from telegram_bot import send_message
    hub_url = str(request.base_url).rstrip("/")
    ok = await send_message(chat_id, f"✅ <b>AM Hub подключён!</b>\nМенеджер: {user.name}\n<a href='{hub_url}'>Открыть хаб →</a>")
    if not ok: return {"ok": False, "error": "Не удалось отправить сообщение. Проверьте chat_id и что бот запущен."}

    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    if sub:
        sub.chat_id = chat_id; sub.is_active = True
    else:
        sub = TelegramSubscription(user_id=user.id, chat_id=chat_id)
        db.add(sub)
    db.commit()
    return {"ok": True}



@router.patch("/api/telegram/settings")
async def api_tg_settings(request: Request, db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    if not auth_token: raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user: raise HTTPException(status_code=401)
    body = await request.json()
    from models import TelegramSubscription
    sub = db.query(TelegramSubscription).filter(TelegramSubscription.user_id == user.id).first()
    if not sub: return {"ok": False, "error": "Сначала подключите Telegram"}
    for k in ("notify_overdue","notify_health_drop","notify_tasks","notify_daily"):
        if k in body: setattr(sub, k, body[k])
    db.commit()
    return {"ok": True}


# ============================================================================
# TEAM DASHBOARD (admin only)

@router.get("/api/admin/jobs")
async def api_jobs_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "admin": raise HTTPException(status_code=403)

    jobs = [
        {"id": "mr_sync",      "name": "Merchrules Sync",     "description": "Синхронизация клиентов и задач из Merchrules", "interval": "каждый час"},
        {"id": "checkup",      "name": "Checkup плановый",    "description": "Проверка просроченных чекапов и создание задач", "interval": "08:00 ежедневно"},
        {"id": "churn",        "name": "Churn Scoring",       "description": "Пересчёт риска оттока для всех клиентов", "interval": "воскресенье 02:00"},
        {"id": "tg_digest",    "name": "Telegram Digest",     "description": "Умный утренний дайджест менеджерам", "interval": "09:00 ежедневно"},
        {"id": "airtable_sync","name": "Airtable Sync",       "description": "Синхронизация клиентов с Airtable", "interval": "каждый час"},
        {"id": "auto_tasks",   "name": "AutoTask Rules",      "description": "Создание задач по правилам автозадач", "interval": "каждые 6 часов"},
    ]

    for j in jobs:
        st = _job_status.get(j["id"], {})
        j["status"]      = st.get("status", "pending")
        j["last_run"]    = st.get("last_run")
        j["next_run"]    = st.get("next_run")
        j["duration_ms"] = st.get("duration_ms")
        j["error"]       = st.get("error")

    return {"jobs": jobs, "log": list(reversed(_job_log[-50:]))}



@router.post("/api/admin/jobs/{job_id}/run")
async def api_job_run(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "admin": raise HTTPException(status_code=403)

    _job_status[job_id] = {"status": "running", "last_run": datetime.utcnow().isoformat()}
    log_job(job_id, f"Запущен вручную пользователем {user.name}")

    import asyncio, time

    async def run():
        start = time.time()
        try:
            if job_id == "churn":
                from churn import recalculate_all
                n = await recalculate_all(db)
                msg = f"Пересчитано: {n} клиентов"
            elif job_id == "mr_sync":
                msg = "Запущен через API — результат в логе"
            elif job_id == "auto_tasks":
                from models import AutoTaskRule
                rules = db.query(AutoTaskRule).filter(AutoTaskRule.is_active == True).all()
                msg = f"Правил обработано: {len(rules)}"
            else:
                msg = f"Job {job_id} не имеет прямого вызова"

            ms = int((time.time() - start) * 1000)
            _job_status[job_id] = {"status": "ok", "last_run": datetime.utcnow().isoformat(), "duration_ms": ms}
            log_job(job_id, f"Завершён за {ms}мс: {msg}")
        except Exception as e:
            _job_status[job_id] = {"status": "error", "last_run": datetime.utcnow().isoformat(), "error": str(e)}
            log_job(job_id, f"Ошибка: {e}", level="error")

    asyncio.create_task(run())
    return {"ok": True}


# ============================================================================
# FILE ATTACHMENTS

@router.get("/api/files/{file_path:path}")
async def api_serve_file(
    file_path: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Serve locally stored files."""
    from storage import get_file
    data = await get_file(file_path)
    if not data: raise HTTPException(status_code=404)
    from fastapi.responses import Response
    return Response(content=data, media_type="application/octet-stream")


# ============================================================================
# EXCEL EXPORT (полноценный)
# ============================================================================

# ============================================================================
# MEETING TRANSCRIPTION + AI SUMMARY

@router.get("/api/onboarding/progress")
async def api_onboarding_progress(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import OnboardingProgress
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    completed = prog.completed if prog else []
    steps = [
        {"id": "hub_url",      "title": "Настройте AM Hub URL",      "done": "hub_url" in completed},
        {"id": "mr_connect",   "title": "Подключите Merchrules",      "done": "mr_connect" in completed},
        {"id": "add_clients",  "title": "Добавьте первых клиентов",   "done": "add_clients" in completed},
        {"id": "first_task",   "title": "Создайте первую задачу",     "done": "first_task" in completed},
        {"id": "tg_connect",   "title": "Подключите Telegram",        "done": "tg_connect" in completed},
        {"id": "first_checkup","title": "Запустите первый чекап",     "done": "first_checkup" in completed},
    ]
    done_count = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done_count, "total": len(steps), "completed": done_count == len(steps)}



@router.post("/api/onboarding/complete-step")
async def api_onboarding_complete_step(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    body = await request.json()
    step = body.get("step", "")
    from models import OnboardingProgress
    from sqlalchemy.orm.attributes import flag_modified
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    if not prog:
        prog = OnboardingProgress(user_id=user.id, completed=[])
        db.add(prog)
    completed = list(prog.completed or [])
    if step and step not in completed:
        completed.append(step)
        prog.completed = completed
        flag_modified(prog, "completed")
    db.commit()
    return {"ok": True, "completed": completed}

# ============================================================================
# REVENUE TRACKING

@router.delete("/api/attachments/{att_id}")
async def api_delete_attachment(
    att_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models import ClientAttachment
    from storage import delete_file
    att = db.query(ClientAttachment).filter(ClientAttachment.id == att_id).first()
    if not att:
        raise HTTPException(status_code=404)
    await delete_file(att.file_key)
    db.delete(att); db.commit()
    return {"ok": True}



@router.get("/api/files/{file_key:path}")
async def api_serve_file(
    file_key: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Отдать файл из local storage."""
    from storage import get_file
    from fastapi.responses import Response as FR
    data = await get_file(file_key)
    if not data:
        raise HTTPException(status_code=404)
    import mimetypes
    mime, _ = mimetypes.guess_type(file_key)
    return FR(content=data, media_type=mime or "application/octet-stream")


# ============================================================================
# MEETING COMMENTS

@router.post("/api/onboarding/skip")
async def api_onboarding_skip(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Пропустить онбординг."""
    from models import OnboardingProgress
    from sqlalchemy.orm.attributes import flag_modified
    prog = db.query(OnboardingProgress).filter(OnboardingProgress.user_id == user.id).first()
    if not prog:
        prog = OnboardingProgress(user_id=user.id, completed=[], skipped=True)
        db.add(prog)
    else:
        prog.skipped = True
        flag_modified(prog, "completed")
    db.commit()
    return {"ok": True}

