"""
Shared auth dependencies — единый источник вместо copy-paste в каждом роуте.
"""
from typing import Optional
from fastapi import Cookie, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User
from auth import decode_access_token


def _get_user_from_cookie(
    auth_token: Optional[str],
    db: Session,
) -> Optional[User]:
    """Получить пользователя из cookie-токена. None если не авторизован."""
    if not auth_token:
        return None
    payload = decode_access_token(auth_token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub", 0))).first()


def require_user(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency — требует авторизации, бросает 401."""
    user = _get_user_from_cookie(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency — требует роль admin."""
    user = _get_user_from_cookie(auth_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return user


def optional_user(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """FastAPI dependency — возвращает пользователя или None."""
    return _get_user_from_cookie(auth_token, db)
