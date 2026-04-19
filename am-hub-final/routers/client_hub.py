"""
Client Hub router — contacts, products, notes pinning for /client/{id} hub.
Extracted to a separate file to avoid merge conflicts in clients.py.
"""
from typing import Optional
from datetime import datetime
import json

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Client, ClientContact, ClientProduct, ClientNote, ClientHistory, User,
    ClientMerchRule, ClientFeed,
)
from auth import decode_access_token

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

def _require_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _require_client(client_id: int, user: User, db: Session) -> Client:
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if user.role == "manager" and client.manager_email != user.email:
        raise HTTPException(status_code=403, detail="Forbidden")
    return client


def _log(db: Session, client_id: int, user_id: Optional[int], field: str,
         old, new, event_type: str = "update") -> None:
    try:
        db.add(ClientHistory(
            client_id=client_id,
            user_id=user_id,
            field=field,
            old_value=json.dumps(old, ensure_ascii=False) if old is not None else None,
            new_value=json.dumps(new, ensure_ascii=False) if new is not None else None,
            event_type=event_type,
        ))
    except Exception:
        pass


def _parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        s = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ── Contacts ─────────────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/contacts")
def list_contacts(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    rows = db.query(ClientContact).filter(ClientContact.client_id == client_id).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "role": c.role,
            "position": c.position,
            "email": c.email,
            "phone": c.phone,
            "telegram": c.telegram,
            "is_primary": bool(c.is_primary),
            "notes": c.notes,
        }
        for c in rows
    ]


@router.post("/api/clients/{client_id}/contacts")
async def create_contact(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    is_primary = bool(body.get("is_primary", False))
    if is_primary:
        db.query(ClientContact).filter(
            ClientContact.client_id == client_id,
            ClientContact.is_primary == True,  # noqa: E712
        ).update({"is_primary": False}, synchronize_session=False)

    contact = ClientContact(
        client_id=client_id,
        name=name,
        role=body.get("role"),
        position=body.get("position"),
        email=body.get("email"),
        phone=body.get("phone"),
        telegram=body.get("telegram"),
        is_primary=is_primary,
        notes=body.get("notes"),
    )
    db.add(contact)
    _log(db, client_id, user.id, "contact", None, name,
         event_type="contact_added")
    db.commit()
    db.refresh(contact)
    return {
        "id": contact.id,
        "name": contact.name,
        "role": contact.role,
        "position": contact.position,
        "email": contact.email,
        "phone": contact.phone,
        "telegram": contact.telegram,
        "is_primary": bool(contact.is_primary),
        "notes": contact.notes,
    }


@router.patch("/api/clients/{client_id}/contacts/{contact_id}")
async def update_contact(
    client_id: int,
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    contact = db.query(ClientContact).filter(
        ClientContact.id == contact_id,
        ClientContact.client_id == client_id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    allowed = {"name", "role", "position", "email", "phone", "telegram",
               "is_primary", "notes"}
    if body.get("is_primary") is True:
        db.query(ClientContact).filter(
            ClientContact.client_id == client_id,
            ClientContact.id != contact_id,
            ClientContact.is_primary == True,  # noqa: E712
        ).update({"is_primary": False}, synchronize_session=False)

    for key, value in body.items():
        if key in allowed:
            if key == "is_primary":
                value = bool(value)
            setattr(contact, key, value)

    db.commit()
    db.refresh(contact)
    return {
        "id": contact.id,
        "name": contact.name,
        "role": contact.role,
        "position": contact.position,
        "email": contact.email,
        "phone": contact.phone,
        "telegram": contact.telegram,
        "is_primary": bool(contact.is_primary),
        "notes": contact.notes,
    }


@router.delete("/api/clients/{client_id}/contacts/{contact_id}")
def delete_contact(
    client_id: int,
    contact_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    contact = db.query(ClientContact).filter(
        ClientContact.id == contact_id,
        ClientContact.client_id == client_id,
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(contact)
    db.commit()
    return {"ok": True}


# ── Products ─────────────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/products")
def list_products(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    rows = db.query(ClientProduct).filter(ClientProduct.client_id == client_id).all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "status": p.status,
            "activated_at": p.activated_at.isoformat() if p.activated_at else None,
            "extra": p.extra or {},
        }
        for p in rows
    ]


@router.post("/api/clients/{client_id}/products")
async def upsert_product(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    code = (body.get("code") or "").strip()
    name = (body.get("name") or "").strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="code and name are required")

    status_val = body.get("status") or "active"
    activated_at = _parse_dt(body.get("activated_at"))
    extra = body.get("extra") or {}

    existing = db.query(ClientProduct).filter(
        ClientProduct.client_id == client_id,
        ClientProduct.code == code,
    ).first()

    if existing:
        existing.status = status_val
        existing.name = name
        existing.extra = extra
        if activated_at is not None:
            existing.activated_at = activated_at
        _log(db, client_id, user.id, "product", None, code,
             event_type="product_updated")
        db.commit()
        db.refresh(existing)
        product = existing
    else:
        product = ClientProduct(
            client_id=client_id,
            code=code,
            name=name,
            status=status_val,
            activated_at=activated_at,
            extra=extra,
        )
        db.add(product)
        _log(db, client_id, user.id, "product", None, code,
             event_type="product_added")
        db.commit()
        db.refresh(product)

    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "status": product.status,
        "activated_at": product.activated_at.isoformat() if product.activated_at else None,
        "extra": product.extra or {},
    }


@router.delete("/api/clients/{client_id}/products/{product_id}")
def delete_product(
    client_id: int,
    product_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    product = db.query(ClientProduct).filter(
        ClientProduct.id == product_id,
        ClientProduct.client_id == client_id,
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
    return {"ok": True}


# ── Notes: pin ───────────────────────────────────────────────────────────────

@router.patch("/api/clients/{client_id}/notes/{note_id}/pin")
async def pin_note(
    client_id: int,
    note_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)

    note = db.query(ClientNote).filter(
        ClientNote.id == note_id,
        ClientNote.client_id == client_id,
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")
    pinned = bool(body.get("pinned", False))

    if pinned:
        db.query(ClientNote).filter(
            ClientNote.client_id == client_id,
            ClientNote.id != note_id,
        ).update({"is_pinned": False}, synchronize_session=False)
        note.is_pinned = True
    else:
        note.is_pinned = False

    db.commit()
    db.refresh(note)
    return {"id": note.id, "is_pinned": bool(note.is_pinned)}


# ── Merch rules ──────────────────────────────────────────────────────────────

@router.get("/api/clients/{client_id}/merch-rules")
def list_merch_rules(
    client_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)

    q = db.query(ClientMerchRule).filter(ClientMerchRule.client_id == client_id)
    if status:
        q = q.filter(ClientMerchRule.status == status)
    rules = q.all()

    last_sync = None
    for r in rules:
        if r.last_synced and (last_sync is None or r.last_synced > last_sync):
            last_sync = r.last_synced

    return {
        "rules": [
            {
                "id": r.id,
                "name": r.name,
                "rule_type": r.rule_type,
                "status": r.status,
                "priority": r.priority,
                "last_synced": r.last_synced.isoformat() if r.last_synced else None,
                "merchrules_id": r.merchrules_id,
                "config": r.config,
            }
            for r in rules
        ],
        "last_sync": last_sync.isoformat() if last_sync else None,
        "total": len(rules),
    }


@router.post("/api/clients/{client_id}/merch-rules/sync")
def sync_merch_rules(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    # TODO: реальная синхронизация через merchrules_sync.py
    # пока просто возвращаем статус
    return {
        "status": "not_implemented",
        "message": "Синхронизация правил пока в разработке",
    }


# ── Feeds ────────────────────────────────────────────────────────────────────

def _feed_dict(f: ClientFeed) -> dict:
    return {
        "id": f.id,
        "feed_type": f.feed_type,
        "name": f.name,
        "url": f.url,
        "status": f.status,
        "last_updated": f.last_updated.isoformat() if f.last_updated else None,
        "sku_count": f.sku_count,
        "errors_count": f.errors_count,
        "last_error": f.last_error,
        "schedule": f.schedule,
    }


@router.get("/api/clients/{client_id}/feeds")
def list_feeds(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    rows = db.query(ClientFeed).filter(ClientFeed.client_id == client_id).all()
    return [_feed_dict(f) for f in rows]


@router.post("/api/clients/{client_id}/feeds")
async def create_feed(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    feed_type = (body.get("feed_type") or "").strip()
    if not feed_type:
        raise HTTPException(status_code=400, detail="feed_type is required")

    feed = ClientFeed(
        client_id=client_id,
        feed_type=feed_type,
        name=body.get("name"),
        url=body.get("url"),
        schedule=body.get("schedule"),
        status="ok",
    )
    db.add(feed)
    _log(db, client_id, user.id, "feed", None, feed_type,
         event_type="feed_added")
    db.commit()
    db.refresh(feed)
    return _feed_dict(feed)


@router.patch("/api/clients/{client_id}/feeds/{feed_id}")
async def update_feed(
    client_id: int,
    feed_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    feed = db.query(ClientFeed).filter(
        ClientFeed.id == feed_id,
        ClientFeed.client_id == client_id,
    ).first()
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid body")

    allowed = {"feed_type", "name", "url", "status", "schedule",
               "sku_count", "errors_count", "last_error"}
    for key, value in body.items():
        if key in allowed:
            setattr(feed, key, value)

    db.commit()
    db.refresh(feed)
    return _feed_dict(feed)


@router.post("/api/clients/{client_id}/feeds/{feed_id}/check")
def check_feed(
    client_id: int,
    feed_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    _require_client(client_id, user, db)
    f = db.query(ClientFeed).filter(
        ClientFeed.id == feed_id,
        ClientFeed.client_id == client_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404, detail="Feed not found")
    # TODO: реальная проверка URL — fetch HEAD, парсинг XML/JSON
    f.last_updated = datetime.utcnow()
    f.status = "ok"
    db.commit()
    return {"status": "ok", "last_updated": f.last_updated.isoformat()}
