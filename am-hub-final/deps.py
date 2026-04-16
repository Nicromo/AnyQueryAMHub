"""
Общие зависимости FastAPI — авторизация через cookie, получение текущего пользователя.
Используется во всех роутерах вместо дублирования одинакового кода.
"""
from typing import Optional
from fastapi import Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import User


def get_current_user_from_cookie(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    """
    Зависимость для API-эндпоинтов: возвращает текущего пользователя
    или бросает 401 если токен невалидный.
    """
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_current_user_or_redirect(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
) -> User:
    """
    Зависимость для HTML-страниц: возвращает пользователя
    или бросает redirect на /login.
    Использовать через require_user() внутри роутов.
    """
    if not auth_token:
        raise _LoginRedirect()
    payload = decode_access_token(auth_token)
    if not payload:
        raise _LoginRedirect()
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise _LoginRedirect()
    return user


class _LoginRedirect(Exception):
    """Внутреннее исключение для редиректа на логин."""
    pass


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Удобная функция для использования в HTML-роутах.
    Возвращает редирект на /login если не авторизован.
    """
    from typing import Optional as Opt
    token = request.cookies.get("auth_token")
    if not token:
        return None  # роут сам решит что делать
    payload = decode_access_token(token)
    if not payload:
        return None
    return db.query(User).filter(User.id == int(payload.get("sub"))).first()
