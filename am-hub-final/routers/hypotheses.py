from typing import Optional, List
from datetime import datetime, date
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, User, Hypothesis

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = {"draft", "testing", "proven", "rejected", "paused"}


def _require_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


def _serialize(h: Hypothesis) -> dict:
    return {
        "id": h.id,
        "client_id": h.client_id,
        "created_by": h.created_by,
        "title": h.title,
        "description": h.description,
        "hypothesis_type": h.hypothesis_type,
        "status": h.status,
        "priority": h.priority,
        "metrics": h.metrics,
        "result": h.result,
        "expected_impact": h.expected_impact,
        "actual_impact": h.actual_impact,
        "start_date": h.start_date.isoformat() if h.start_date else None,
        "end_date": h.end_date.isoformat() if h.end_date else None,
        "tags": h.tags or [],
        "meta": h.meta or {},
        "created_at": h.created_at.isoformat() if h.created_at else None,
        "updated_at": h.updated_at.isoformat() if h.updated_at else None,
    }


@router.get("/api/hypotheses")
async def list_hypotheses(
    client_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    q = db.query(Hypothesis)
    if client_id is not None:
        q = q.filter(Hypothesis.client_id == client_id)
    if status:
        q = q.filter(Hypothesis.status == status)
    total = q.count()
    page = max(1, page)
    items = q.order_by(Hypothesis.created_at.desc()).offset((page - 1) * 50).limit(50).all()
    return {
        "total": total,
        "page": page,
        "pages": (total + 49) // 50,
        "items": [_serialize(h) for h in items],
    }


@router.post("/api/hypotheses")
async def create_hypothesis(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    data = await request.json()
    if not data.get("title"):
        raise HTTPException(status_code=400, detail="title is required")

    def _parse_date(v):
        if not v:
            return None
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    h = Hypothesis(
        client_id=data.get("client_id") or None,
        created_by=user.email,
        title=data["title"],
        description=data.get("description"),
        hypothesis_type=data.get("hypothesis_type", "ab"),
        status=data.get("status", "draft"),
        priority=data.get("priority", "medium"),
        metrics=data.get("metrics"),
        expected_impact=data.get("expected_impact"),
        start_date=_parse_date(data.get("start_date")),
        end_date=_parse_date(data.get("end_date")),
        tags=data.get("tags") or [],
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"ok": True, "id": h.id, "hypothesis": _serialize(h)}


@router.get("/api/hypotheses/{hypothesis_id}")
async def get_hypothesis(
    hypothesis_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    h = db.query(Hypothesis).filter(Hypothesis.id == hypothesis_id).first()
    if not h:
        raise HTTPException(status_code=404)
    return _serialize(h)


@router.patch("/api/hypotheses/{hypothesis_id}")
async def update_hypothesis(
    hypothesis_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    h = db.query(Hypothesis).filter(Hypothesis.id == hypothesis_id).first()
    if not h:
        raise HTTPException(status_code=404)
    data = await request.json()
    allowed = {
        "title", "description", "hypothesis_type", "status", "priority",
        "metrics", "result", "expected_impact", "actual_impact",
        "start_date", "end_date", "tags", "meta",
    }
    for k, v in data.items():
        if k not in allowed:
            continue
        if k in ("start_date", "end_date"):
            setattr(h, k, date.fromisoformat(v) if v else None)
        else:
            setattr(h, k, v)
    h.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "hypothesis": _serialize(h)}


@router.delete("/api/hypotheses/{hypothesis_id}")
async def delete_hypothesis(
    hypothesis_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    h = db.query(Hypothesis).filter(Hypothesis.id == hypothesis_id).first()
    if not h:
        raise HTTPException(status_code=404)
    if user.role != "admin" and h.created_by != user.email:
        raise HTTPException(status_code=403, detail="Only owner or admin can delete")
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.post("/api/hypotheses/{hypothesis_id}/status")
async def change_hypothesis_status(
    hypothesis_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    data = await request.json()
    new_status = data.get("status")
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(VALID_STATUSES)}")
    h = db.query(Hypothesis).filter(Hypothesis.id == hypothesis_id).first()
    if not h:
        raise HTTPException(status_code=404)
    h.status = new_status
    h.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": h.id, "status": h.status}


@router.get("/api/clients/{client_id}/hypotheses")
async def client_hypotheses(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)
    items = (
        db.query(Hypothesis)
        .filter(Hypothesis.client_id == client_id)
        .order_by(Hypothesis.created_at.desc())
        .all()
    )
    return {"client_id": client_id, "total": len(items), "items": [_serialize(h) for h in items]}
