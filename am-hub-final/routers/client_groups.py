"""
Client Groups (ГК) — группы компаний.

Один клиент (Client) может быть привязан к ГК через group_id.
В UI портфеля есть toggle «объединять ГК» — клиенты схлопываются под заголовок
группы с агрегатами (сумма MRR + GMV, кол-во, health avg).
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, List
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, ClientGroup, User

logger = logging.getLogger(__name__)
router = APIRouter()


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


def _require_edit(u: User):
    if u.role not in ("admin", "grouphead"):
        raise HTTPException(403, "admin/grouphead only")


def _serialize(g: ClientGroup, db: Session, include_members: bool = False) -> dict:
    clients = (db.query(Client).filter(Client.group_id == g.id).all()
               if g.id is not None else [])
    mrr = sum(c.mrr or 0 for c in clients)
    gmv = sum(c.gmv or 0 for c in clients)
    health_vals = [c.health_score for c in clients if c.health_score is not None]
    avg_health = round(sum(health_vals) / len(health_vals), 3) if health_vals else None
    segs = sorted({(c.segment or "—") for c in clients})
    managers = sorted({c.manager_email for c in clients if c.manager_email})
    data = {
        "id": g.id,
        "name": g.name,
        "description": g.description or "",
        "segment": g.segment,
        "manager_email": g.manager_email,
        "members_count": len(clients),
        "mrr": round(mrr, 2),
        "gmv": round(gmv, 2),
        "avg_health": avg_health,
        "segments": segs,
        "managers": managers,
        "created_by": g.created_by,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }
    if include_members:
        data["members"] = [{
            "id": c.id, "name": c.name, "segment": c.segment,
            "mrr": c.mrr, "gmv": c.gmv,
            "manager_email": c.manager_email,
            "health_score": c.health_score,
        } for c in clients]
    return data


@router.get("/api/client-groups")
async def list_groups(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    include_members: bool = False,
):
    _user(auth_token, db)
    groups = db.query(ClientGroup).order_by(ClientGroup.name).all()
    return {"items": [_serialize(g, db, include_members=include_members) for g in groups]}


@router.get("/api/client-groups/{group_id}")
async def get_group(
    group_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    g = db.query(ClientGroup).filter(ClientGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    return _serialize(g, db, include_members=True)


@router.post("/api/client-groups")
async def create_group(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _require_edit(u)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    if db.query(ClientGroup).filter(ClientGroup.name == name).first():
        raise HTTPException(409, "group name already exists")
    g = ClientGroup(
        name=name,
        description=(data.get("description") or "").strip() or None,
        segment=data.get("segment"),
        manager_email=data.get("manager_email"),
        created_by=u.email,
    )
    db.add(g); db.flush()
    ids = data.get("client_ids") or []
    if ids:
        (db.query(Client).filter(Client.id.in_([int(x) for x in ids]))
         .update({"group_id": g.id}, synchronize_session=False))
    db.commit()
    db.refresh(g)
    return _serialize(g, db, include_members=True)


@router.patch("/api/client-groups/{group_id}")
async def update_group(
    group_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _require_edit(u)
    g = db.query(ClientGroup).filter(ClientGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    data = await request.json()
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        existing = db.query(ClientGroup).filter(ClientGroup.name == name,
                                                 ClientGroup.id != group_id).first()
        if existing:
            raise HTTPException(409, "name already used")
        g.name = name
    if "description" in data:
        g.description = (data["description"] or "").strip() or None
    if "segment" in data:
        g.segment = data["segment"] or None
    if "manager_email" in data:
        g.manager_email = data["manager_email"] or None
    db.commit()
    db.refresh(g)
    return _serialize(g, db, include_members=True)


@router.delete("/api/client-groups/{group_id}")
async def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _require_edit(u)
    g = db.query(ClientGroup).filter(ClientGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    (db.query(Client).filter(Client.group_id == group_id)
     .update({"group_id": None}, synchronize_session=False))
    db.delete(g)
    db.commit()
    return {"ok": True}


@router.post("/api/client-groups/{group_id}/members")
async def set_members(
    group_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """mode='add' — добавить client_ids к группе; mode='set' — заменить полностью."""
    u = _user(auth_token, db)
    _require_edit(u)
    g = db.query(ClientGroup).filter(ClientGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    data = await request.json()
    ids: List[int] = [int(x) for x in (data.get("client_ids") or [])]
    mode = (data.get("mode") or "add").lower()

    if mode == "set":
        q = db.query(Client).filter(Client.group_id == group_id)
        if ids:
            q = q.filter(~Client.id.in_(ids))
        q.update({"group_id": None}, synchronize_session=False)
    if ids:
        (db.query(Client).filter(Client.id.in_(ids))
         .update({"group_id": group_id}, synchronize_session=False))
    db.commit()
    db.refresh(g)
    return _serialize(g, db, include_members=True)


@router.delete("/api/client-groups/{group_id}/members/{client_id}")
async def remove_member(
    group_id: int, client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _require_edit(u)
    c = db.query(Client).filter(Client.id == client_id,
                                 Client.group_id == group_id).first()
    if not c:
        raise HTTPException(404, "client not in this group")
    c.group_id = None
    db.commit()
    return {"ok": True}
