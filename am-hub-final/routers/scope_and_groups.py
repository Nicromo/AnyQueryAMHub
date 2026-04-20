"""
Scope switcher (per-request cookie) + admin CRUD по группам менеджеров.
"""
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import ManagerGroup, User
from auth import decode_access_token
from scope import available_scopes, default_scope, resolve_scope

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


def _admin(u: User):
    if (u.role or "").lower() != "admin":
        raise HTTPException(403, "admin only")


@router.get("/api/me/scope")
async def me_scope(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    am_scope: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    allowed = available_scopes(u)
    active = resolve_scope(u, am_scope)
    group_name = None
    if u.group_id:
        g = db.query(ManagerGroup).filter(ManagerGroup.id == u.group_id).first()
        group_name = g.name if g else None
    return {
        "role": u.role,
        "group_id": u.group_id,
        "group_name": group_name,
        "available_scopes": allowed,
        "active": active,
    }


@router.post("/api/me/scope")
async def me_scope_set(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    try:
        data = await request.json()
    except Exception:
        data = {}
    requested = str(data.get("scope", "")).lower()
    if requested not in available_scopes(u):
        raise HTTPException(400, f"scope '{requested}' not allowed for role {u.role}")
    resp = JSONResponse({"ok": True, "active": requested})
    resp.set_cookie("am_scope", requested, max_age=60 * 60 * 24 * 30,
                    httponly=False, samesite="lax")
    return resp


# ── Admin: manager groups CRUD ───────────────────────────────────────────────

@router.get("/api/admin/groups")
async def groups_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _admin(u)
    rows = db.query(ManagerGroup).order_by(ManagerGroup.name).all()
    result = []
    for g in rows:
        members = (db.query(User)
                     .filter(User.group_id == g.id, User.is_active == True)
                     .all())
        result.append({
            "id": g.id,
            "name": g.name,
            "grouphead_id": g.grouphead_id,
            "members": [{"id": m.id, "email": m.email, "role": m.role} for m in members],
        })
    return result


@router.post("/api/admin/groups")
async def groups_create(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _admin(u)
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    grouphead_id = data.get("grouphead_id")
    g = ManagerGroup(name=name, grouphead_id=grouphead_id)
    db.add(g)
    db.commit()
    db.refresh(g)
    return {"id": g.id, "name": g.name, "grouphead_id": g.grouphead_id}


@router.put("/api/admin/groups/{group_id}")
async def groups_update(
    group_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _admin(u)
    g = db.query(ManagerGroup).filter(ManagerGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    data = await request.json()
    if "name" in data:
        g.name = str(data["name"]).strip() or g.name
    if "grouphead_id" in data:
        g.grouphead_id = data["grouphead_id"]
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/groups/{group_id}")
async def groups_delete(
    group_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    _admin(u)
    g = db.query(ManagerGroup).filter(ManagerGroup.id == group_id).first()
    if not g:
        raise HTTPException(404)
    # Отвязываем менеджеров из группы
    db.query(User).filter(User.group_id == group_id).update({"group_id": None})
    db.delete(g)
    db.commit()
    return {"ok": True}


@router.get("/api/admin/users")
async def admin_users_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Плоский JSON-список пользователей для UI назначения ролей/групп."""
    u = _user(auth_token, db)
    _admin(u)
    rows = db.query(User).order_by(User.email).all()
    return [{"id": r.id, "email": r.email, "role": r.role,
             "is_active": bool(r.is_active), "group_id": r.group_id,
             "telegram_id": r.telegram_id}
            for r in rows]


@router.put("/api/admin/users/{user_id}/group")
async def user_set_group(
    user_id: int, request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Админ меняет группу и/или роль пользователя."""
    u = _user(auth_token, db)
    _admin(u)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404)
    data = await request.json()
    if "group_id" in data:
        target.group_id = data["group_id"]
    if "role" in data:
        r = str(data["role"]).lower()
        if r not in ("admin", "manager", "viewer", "grouphead", "leadership"):
            raise HTTPException(400, f"unknown role: {r}")
        target.role = r
    db.commit()
    return {"ok": True, "role": target.role, "group_id": target.group_id}
