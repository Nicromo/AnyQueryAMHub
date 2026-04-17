"""Auto-split from misc.py"""
from typing import Optional, List
from datetime import datetime, timedelta
import os, json, logging

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
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

def _checkup_auth(auth_token: Optional[str], db, request=None):
    bearer = ""
    if request:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    token = bearer or auth_token
    if not token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user

@router.get("/api/checkup/{cabinet_id}/queries")
async def api_checkup_queries(
    cabinet_id: str,
    request: Request,
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
    user = _checkup_auth(auth_token, db, request)

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
    user = _checkup_auth(auth_token, db, request)

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
    request: Request,
    limit: int = 10,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """История чекапов клиента."""
    user = _checkup_auth(auth_token, db, request)

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




