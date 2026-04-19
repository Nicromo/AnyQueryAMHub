"""
API-токены для внешних клиентов (браузерное расширение и т.п.).

Хранятся в user.settings["api_tokens"] как список словарей:
    {id, name, prefix, hashed, created_at, last_used_at}

Сырой токен отдаётся ТОЛЬКО один раз — при создании. Дальше — только sha256-хэш.
Формат токена: "amh_<32-char urlsafe base64>".
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database import get_db
from models import User

router = APIRouter()

TOKEN_PREFIX = "amh_"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def generate_api_token() -> str:
    """Новый сырой токен. Формат: amh_<43-char urlsafe>."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def find_user_by_api_token(db: Session, raw_token: str) -> Optional[User]:
    """Найти пользователя по сырому API-токену. Обновляет last_used_at."""
    if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
        return None
    hashed = _sha256(raw_token)
    # Линейный скан — OK для сотен пользователей. На большем масштабе
    # перенести api_tokens в отдельную таблицу с индексом по hashed.
    users = db.query(User).filter(User.is_active == True).all()  # noqa: E712
    for u in users:
        tokens = (u.settings or {}).get("api_tokens", [])
        for t in tokens:
            if t.get("hashed") == hashed and not t.get("revoked"):
                t["last_used_at"] = datetime.utcnow().isoformat()
                flag_modified(u, "settings")
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                return u
    return None


def _current_user_from_cookie(db: Session, auth_token: Optional[str]) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401)
    return user


def resolve_user(db: Session, request, auth_token_cookie: Optional[str]) -> Optional[User]:
    """
    Единая функция авторизации для эндпоинтов, которые могут вызываться
    и из браузера (cookie JWT), и из расширения (Bearer amh_... или JWT).
    Возвращает User или None (пусть вызывающий сам решает 401).
    """
    # 1) Cookie JWT
    if auth_token_cookie:
        try:
            from auth import decode_access_token
            payload = decode_access_token(auth_token_cookie)
            if payload:
                u = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
                if u and u.is_active:
                    return u
        except Exception:
            pass
    # 2) Authorization: Bearer <token>
    if request is not None:
        auth_header = request.headers.get("Authorization", "") if hasattr(request, "headers") else ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if token.startswith(TOKEN_PREFIX):
                u = find_user_by_api_token(db, token)
                if u:
                    return u
            else:
                try:
                    from auth import decode_access_token
                    payload = decode_access_token(token)
                    if payload:
                        u = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
                        if u and u.is_active:
                            return u
                except Exception:
                    pass
    return None


def _sanitize(tok: dict) -> dict:
    """То что безопасно показать в списке (без хэша и без сырого токена)."""
    return {
        "id": tok.get("id"),
        "name": tok.get("name", ""),
        "prefix": tok.get("prefix", ""),
        "created_at": tok.get("created_at"),
        "last_used_at": tok.get("last_used_at"),
    }


@router.get("/api/me/api-tokens")
async def list_api_tokens(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _current_user_from_cookie(db, auth_token)
    tokens = (user.settings or {}).get("api_tokens", [])
    return {
        "tokens": [_sanitize(t) for t in tokens if not t.get("revoked")]
    }


@router.post("/api/me/api-tokens")
async def create_api_token(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Создать новый API-токен. Возвращает сырой token ровно один раз."""
    user = _current_user_from_cookie(db, auth_token)
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body.get("name") or "").strip() or "Без названия"
    if len(name) > 64:
        name = name[:64]

    raw = generate_api_token()
    # prefix — что показываем в списке для идентификации
    prefix = raw[:12]  # "amh_" + 8 chars
    token_entry = {
        "id": uuid.uuid4().hex,
        "name": name,
        "prefix": prefix,
        "hashed": _sha256(raw),
        "created_at": datetime.utcnow().isoformat(),
        "last_used_at": None,
    }

    settings = dict(user.settings or {})
    tokens = list(settings.get("api_tokens", []))
    # Лимит — защита от мусора
    if len([t for t in tokens if not t.get("revoked")]) >= 20:
        raise HTTPException(status_code=400, detail="Max 20 active tokens")
    tokens.append(token_entry)
    settings["api_tokens"] = tokens
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()

    return {
        "ok": True,
        "id": token_entry["id"],
        "name": name,
        "prefix": prefix,
        "token": raw,  # единственный раз отдаём полный токен
        "warning": "Сохрани этот токен сейчас — он больше не будет показан",
    }


@router.delete("/api/me/api-tokens/{token_id}")
async def revoke_api_token(
    token_id: str,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _current_user_from_cookie(db, auth_token)
    settings = dict(user.settings or {})
    tokens = list(settings.get("api_tokens", []))
    found = False
    for t in tokens:
        if t.get("id") == token_id:
            t["revoked"] = True
            t["revoked_at"] = datetime.utcnow().isoformat()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404)
    settings["api_tokens"] = tokens
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}
