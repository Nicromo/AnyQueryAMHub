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
    FollowupTemplate, VoiceNote, ClientHistory, CHECKUP_INTERVALS,
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
    queries = list(checkup_queries.get(type, []) or [])
    source = "saved" if queries else None

    # Главный источник: Merchrules /analytics/{top|zero|null}_queries для site_id клиента.
    # Унифицированный endpoint — работает для любого клиента с настроенным
    # merchrules_account_id + user-уровневыми или env-кредами MR.
    if not queries and client.merchrules_account_id:
        try:
            from merchrules_sync import fetch_checkup_queries
            # Креды текущего менеджера имеют приоритет над env
            mr_settings = (user.settings or {}).get("merchrules", {}) or {}
            login = mr_settings.get("login") or ""
            try:
                from crypto import dec as _dec
                password = _dec(mr_settings.get("password", "")) or ""
            except Exception:
                password = mr_settings.get("password") or ""
            queries = await fetch_checkup_queries(
                site_id=client.merchrules_account_id,
                type_=type, login=login, password=password,
            )
            if queries:
                source = "merchrules"
        except Exception as e:
            logger.warning(f"merchrules queries fetch failed: {e}")

    # Fallback: уникальные запросы из последнего CheckupResult
    if not queries:
        try:
            from models import CheckupResult
            last = (db.query(CheckupResult)
                      .filter(CheckupResult.client_id == client.id,
                              CheckupResult.query_type == type)
                      .order_by(CheckupResult.created_at.desc())
                      .first())
            if last and last.results:
                seen: set = set()
                for r in last.results:
                    q = (r.get("query") or "").strip()
                    if q and q not in seen:
                        seen.add(q)
                        queries.append(q)
                if queries:
                    source = "last_result"
        except Exception as e:
            logger.debug(f"checkup queries fallback failed: {e}")

    if not source:
        source = "empty"

    return {
        "ok": True,
        "queries": queries,
        "type": type,
        "client": client.name,
        "source": source,
    }


@router.post("/api/checkup/{cabinet_id}/queries")
async def api_checkup_queries_save(
    cabinet_id: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сохраняет вручную введённые запросы в integration_metadata[checkup_queries][type].
    Расширение вызывает после того, как менеджер ввёл список запросов в textarea —
    чтобы следующий раз подтянулись автоматически.

    Body: {type: "top"|"random"|"zero"|"zeroquery", queries: ["пуховик", ...]}
    """
    user = _checkup_auth(auth_token, db, request)

    client = None
    if cabinet_id.isdigit():
        client = db.query(Client).filter(Client.id == int(cabinet_id)).first()
    if not client:
        client = db.query(Client).filter(Client.merchrules_account_id == cabinet_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Кабинет не найден")

    body = await request.json()
    type_ = (body.get("type") or "top").lower()
    queries = body.get("queries") or []
    if isinstance(queries, str):
        queries = [q.strip() for q in queries.splitlines() if q.strip()]
    if not isinstance(queries, list):
        raise HTTPException(400, "queries must be list")
    queries = [str(q).strip() for q in queries if str(q).strip()][:100]

    meta = dict(client.integration_metadata or {})
    cq = dict(meta.get("checkup_queries") or {})
    cq[type_] = queries
    meta["checkup_queries"] = cq
    client.integration_metadata = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(client, "integration_metadata")
    db.commit()
    return {"ok": True, "saved": len(queries), "type": type_}




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

    # Уведомляем менеджера в inbox: результат чекапа готов (особенно если средняя оценка низкая)
    try:
        if client.manager_email:
            mgr = db.query(User).filter(
                User.email == client.manager_email, User.is_active == True
            ).first()
            if mgr:
                from tg_notifications import notify_manager
                summary = (f"avg {avg_score:.2f} по {len(results)} запросам"
                           if avg_score is not None else f"{len(results)} запросов")
                if avg_score is not None and avg_score < 1.5:
                    summary += " · критичный уровень"
                await notify_manager(db, mgr, "checkup_result",
                    {"client": client.name, "summary": summary},
                    related_type="client", related_id=client.id)
                db.commit()
    except Exception as _ne:
        logger.warning(f"notify checkup_result skipped: {_ne}")

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
    try:
        body = await request.json()
    except Exception as _e:
        logger.debug(f"request body parse: {_e}")

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






# ─── Checkup v2 — чекапы качества поиска + Diginetica ──────────────────────

def _require_user_v2(auth_token, db, request=None):
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


def _checkup_dict(c):
    return {
        "id": c.id, "client_id": c.client_id, "name": c.name,
        "frequency": c.frequency, "due_date": c.due_date.isoformat() if c.due_date else None,
        "partner_comment": c.partner_comment, "any_comment": c.any_comment,
        "status": c.status, "score": c.score, "score_max": c.score_max,
        "tracking": c.tracking or {}, "uiux": c.uiux or {},
        "recs": c.recs or {}, "reviews": c.reviews or {},
        "products_tab": c.products_tab or {}, "debts": c.debts or {},
        "search_comment": c.search_comment, "top_queries_comment": c.top_queries_comment,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "created_by": c.created_by,
    }


def _query_dict(q):
    return {
        "id": q.id, "checkup_id": q.checkup_id, "group": q.group,
        "query": q.query, "shows_count": q.shows_count, "score": q.score,
        "problem": q.problem, "solution": q.solution,
        "partner_comment": q.partner_comment,
        "response_time_ms": q.response_time_ms, "results_count": q.results_count,
        "has_correction": q.has_correction,
        "checked_at": q.checked_at.isoformat() if q.checked_at else None,
    }


@router.post("/api/clients/{client_id}/checkups")
async def v2_create_checkup(
    client_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    user = _require_user_v2(auth_token, db, request)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    body = await request.json()
    from models import CheckupV2
    due = None
    if body.get("due_date"):
        try:
            due = datetime.fromisoformat(str(body["due_date"])[:19])
        except Exception as _e:
            logger.warning(f"due_date parse failed ({body.get('due_date')!r}): {_e}")
    c = CheckupV2(
        client_id=client_id,
        name=body.get("name") or f"Чек-ап {datetime.utcnow().strftime('%b %Y')}",
        frequency=body.get("frequency") or "monthly",
        due_date=due,
        partner_comment=body.get("partner_comment"),
        any_comment=body.get("any_comment"),
        status="draft",
        created_by=user.email,
    )
    db.add(c); db.commit(); db.refresh(c)
    return _checkup_dict(c)


@router.get("/api/clients/{client_id}/checkups")
async def v2_list_checkups(
    client_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2
    rows = db.query(CheckupV2).filter(CheckupV2.client_id == client_id).order_by(CheckupV2.created_at.desc()).all()
    return {"checkups": [_checkup_dict(c) for c in rows]}


@router.get("/api/checkups/{checkup_id}")
async def v2_get_checkup(
    checkup_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2, CheckupQuery
    c = db.query(CheckupV2).filter(CheckupV2.id == checkup_id).first()
    if not c: raise HTTPException(status_code=404)
    queries = db.query(CheckupQuery).filter(CheckupQuery.checkup_id == checkup_id).all()
    return {"checkup": _checkup_dict(c), "queries": [_query_dict(q) for q in queries]}


@router.patch("/api/checkups/{checkup_id}")
async def v2_update_checkup(
    checkup_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2
    c = db.query(CheckupV2).filter(CheckupV2.id == checkup_id).first()
    if not c: raise HTTPException(status_code=404)
    body = await request.json()
    for field in ("name", "frequency", "partner_comment", "any_comment",
                  "status", "search_comment", "top_queries_comment",
                  "tracking", "uiux", "recs", "reviews", "products_tab", "debts"):
        if field in body:
            setattr(c, field, body[field])
    if "due_date" in body and body["due_date"]:
        try:
            c.due_date = datetime.fromisoformat(str(body["due_date"])[:19])
        except Exception as _e:
            logger.warning(f"update due_date parse failed ({body.get('due_date')!r}): {_e}")
    db.commit(); db.refresh(c)
    return _checkup_dict(c)


@router.delete("/api/checkups/{checkup_id}")
async def v2_delete_checkup(
    checkup_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2
    db.query(CheckupV2).filter(CheckupV2.id == checkup_id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.post("/api/checkups/{checkup_id}/queries")
async def v2_add_queries(
    checkup_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2, CheckupQuery
    c = db.query(CheckupV2).filter(CheckupV2.id == checkup_id).first()
    if not c: raise HTTPException(status_code=404)
    body = await request.json()
    group = body.get("group", "top")
    queries = body.get("queries", []) or []
    added = 0
    for q in queries:
        if isinstance(q, str):
            q = {"query": q}
        text = (q.get("query") or "").strip()
        if not text: continue
        db.add(CheckupQuery(
            checkup_id=checkup_id, group=group,
            query=text, shows_count=int(q.get("shows_count") or 0),
        ))
        added += 1
    if c.status == "draft":
        c.status = "in_progress"
    db.commit()
    return {"ok": True, "added": added}


@router.patch("/api/checkup-queries/{qid}")
async def v2_update_query(
    qid: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupQuery
    q = db.query(CheckupQuery).filter(CheckupQuery.id == qid).first()
    if not q: raise HTTPException(status_code=404)
    body = await request.json()
    for field in ("score", "problem", "solution", "partner_comment",
                  "shows_count", "query"):
        if field in body:
            setattr(q, field, body[field])
    db.commit()
    return _query_dict(q)


@router.delete("/api/checkup-queries/{qid}")
async def v2_delete_query(
    qid: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    _require_user_v2(auth_token, db, request)
    from models import CheckupQuery
    db.query(CheckupQuery).filter(CheckupQuery.id == qid).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.post("/api/checkups/{checkup_id}/run")
async def v2_run_checkup(
    checkup_id: int, request: Request,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    """Прогнать все запросы чекапа через Diginetica. Записать оценки и пересчитать avg."""
    _require_user_v2(auth_token, db, request)
    from models import CheckupV2, CheckupQuery
    from integrations.diginetica import run_search, auto_score
    c = db.query(CheckupV2).filter(CheckupV2.id == checkup_id).first()
    if not c: raise HTTPException(status_code=404)
    client = db.query(Client).filter(Client.id == c.client_id).first()
    api_key = getattr(client, "diginetica_api_key", None) if client else None
    if not api_key:
        return {"ok": False, "error": "У клиента не задан diginetica_api_key"}
    queries = db.query(CheckupQuery).filter(CheckupQuery.checkup_id == checkup_id).all()
    scored = 0
    scores_sum = 0
    scores_count = 0
    for q in queries:
        res = await run_search(api_key, q.query)
        sc = auto_score(res)
        q.score = sc
        q.diginetica_response = (res or {}).get("raw") or {}
        q.response_time_ms = res.get("response_time_ms")
        q.results_count = res.get("results_count", 0) or 0
        q.has_correction = bool(res.get("has_correction"))
        q.checked_at = datetime.utcnow()
        if sc is not None:
            scores_sum += sc
            scores_count += 1
        scored += 1
    if scores_count:
        c.score = round(scores_sum / scores_count, 2)
    if c.status == "draft" or c.status == "in_progress":
        c.status = "done"
    db.commit()
    return {"ok": True, "scored": scored, "avg_score": c.score}


@router.get("/api/clients/{client_id}/analytics/queries")
async def v2_analytics_queries(
    client_id: int,
    period_days: int = 30,
    limit: int = 30,
    q_type: str = "top",
    request: Request = None,
    db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None),
):
    """Поисковые запросы клиента из аналитики. Пока заглушка с fallback пустого списка.

    TODO: подключить реальный источник через Merchrules backend или Diginetica analytics API.
    """
    _require_user_v2(auth_token, db, request)
    # TODO: реальный источник
    return {
        "queries": [],
        "message": "Источник аналитики не настроен — добавляйте запросы вручную или CSV-импортом.",
    }


@router.post("/api/checkup/{checkup_id}/load-queries-from-merchrules")
async def v2_load_queries_from_merchrules(
    checkup_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Загрузить запросы из Merchrules analytics в чекап.

    Body: {"kind": "top|random|null|zero", "limit": 30,
           "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}.
    """
    user = _require_user_v2(auth_token, db, request)

    from models import CheckupV2, CheckupQuery
    from integrations.merchrules_stats import fetch_queries, KIND_TO_ENDPOINT
    import merchrules_sync
    import httpx

    checkup = db.query(CheckupV2).filter(CheckupV2.id == checkup_id).first()
    if not checkup:
        raise HTTPException(status_code=404, detail="Checkup not found")

    client = db.query(Client).filter(Client.id == checkup.client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    site_id = (client.merchrules_account_id or "").strip()
    if not site_id:
        return {
            "ok": False,
            "error": "У клиента не задан merchrules_account_id (site_id) — задайте его в карточке клиента.",
        }

    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    kind = (body.get("kind") or "top").strip()
    if kind not in KIND_TO_ENDPOINT:
        raise HTTPException(status_code=400, detail=f"unknown kind: {kind}")
    try:
        limit = int(body.get("limit") or 30)
    except (TypeError, ValueError):
        limit = 30
    date_from = body.get("date_from") or None
    date_to = body.get("date_to") or None

    settings = (user.settings or {}) if user else {}
    mr = settings.get("merchrules", {}) or {}
    login = mr.get("login") or mr.get("username") or _env("MERCHRULES_LOGIN")
    from crypto import dec as _dec
    password = _dec(mr.get("password", "")) or _env("MERCHRULES_PASSWORD")
    if not login or not password:
        return {
            "ok": False,
            "error": "Нет кредов Merchrules — задайте логин/пароль в Настройках → Интеграции.",
        }

    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            token = await merchrules_sync.get_auth_token(hx, login, password)
        if not token:
            return {"ok": False, "error": "Не удалось авторизоваться в Merchrules."}

        rows = await fetch_queries(
            token=token,
            site_id=site_id,
            kind=kind,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
    except httpx.HTTPError as e:
        logger.warning("Merchrules analytics HTTP error: %s", e)
        return {"ok": False, "error": f"Merchrules API error: {e}"}
    except Exception as e:
        logger.exception("Merchrules analytics fetch error")
        return {"ok": False, "error": str(e)[:200]}

    added = 0
    for row in rows:
        q_text = (row.get("query") or "").strip()
        if not q_text:
            continue
        db.add(CheckupQuery(
            checkup_id=checkup_id,
            group=kind,
            query=q_text,
            shows_count=int(row.get("count") or 0),
        ))
        added += 1

    if added and checkup.status == "draft":
        checkup.status = "in_progress"
    db.commit()

    return {"ok": True, "count": added, "kind": kind, "site_id": site_id}
