"""
Per-client roadmap — Task.source='roadmap' с meta.roadmap_quarter (q1..q4/backlog).

Используется на странице клиента (/design/client/{id}) для персонального планирования
работ по кварталам.

Endpoints:
  GET   /api/clients/{id}/roadmap-tasks           → группированы по кварталам
  POST  /api/clients/{id}/roadmap-tasks           → создать задачу в колонке
  PATCH /api/clients/{id}/roadmap-tasks/{task_id} → перенос между кварталами + title
  DELETE /api/clients/{id}/roadmap-tasks/{task_id}
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database import get_db
from auth import decode_access_token
from models import Client, Task, User

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_QUARTERS = {"q1", "q2", "q3", "q4", "backlog"}


def _user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(401)
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    u = db.query(User).filter(User.id == int(p.get("sub"))).first()
    if not u:
        raise HTTPException(401)
    return u


def _quarter_of(t: Task) -> str:
    meta = t.meta or {}
    q = (meta.get("roadmap_quarter") or "backlog").lower()
    return q if q in ALLOWED_QUARTERS else "backlog"


def _serialize_task(t: Task) -> dict:
    meta = t.meta or {}
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description or "",
        "status": t.status,
        "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "roadmap_quarter": _quarter_of(t),
        "order_idx": int(meta.get("roadmap_order_idx") or 0),
    }


@router.get("/api/clients/{client_id}/roadmap-tasks")
async def list_roadmap_tasks(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    tasks = (db.query(Task)
               .filter(Task.client_id == client_id, Task.source == "roadmap")
               .all())
    return {"items": [_serialize_task(t) for t in tasks]}


@router.post("/api/clients/{client_id}/roadmap-tasks")
async def create_roadmap_task(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "client not found")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    quarter = (body.get("roadmap_quarter") or "backlog").lower()
    if quarter not in ALLOWED_QUARTERS:
        quarter = "backlog"

    t = Task(
        client_id=client_id,
        title=title,
        description=body.get("description") or "",
        status="plan",
        priority=body.get("priority") or "medium",
        source="roadmap",
        meta={"roadmap_quarter": quarter, "roadmap_order_idx": 0,
              "created_by": u.email},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize_task(t)


@router.patch("/api/clients/{client_id}/roadmap-tasks/{task_id}")
async def patch_roadmap_task(
    client_id: int, task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    t = (db.query(Task)
           .filter(Task.id == task_id, Task.client_id == client_id,
                   Task.source == "roadmap")
           .first())
    if not t:
        raise HTTPException(404)
    body = await request.json()
    meta = dict(t.meta or {})
    changed_meta = False
    if "roadmap_quarter" in body:
        q = (body["roadmap_quarter"] or "backlog").lower()
        if q not in ALLOWED_QUARTERS:
            raise HTTPException(400, f"invalid roadmap_quarter: {q}")
        meta["roadmap_quarter"] = q
        changed_meta = True
    if "order_idx" in body:
        try:
            meta["roadmap_order_idx"] = int(body["order_idx"])
            changed_meta = True
        except Exception:
            pass
    if "title" in body:
        tt = (body["title"] or "").strip()
        if not tt:
            raise HTTPException(400, "title cannot be empty")
        t.title = tt
    if "description" in body:
        t.description = body["description"] or ""
    if "status" in body:
        t.status = body["status"]
    if changed_meta:
        t.meta = meta
        flag_modified(t, "meta")
    db.commit()
    db.refresh(t)
    return _serialize_task(t)


@router.delete("/api/clients/{client_id}/roadmap-tasks/{task_id}")
async def delete_roadmap_task(
    client_id: int, task_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    t = (db.query(Task)
           .filter(Task.id == task_id, Task.client_id == client_id,
                   Task.source == "roadmap")
           .first())
    if not t:
        raise HTTPException(404)
    db.delete(t)
    db.commit()
    return {"ok": True}
