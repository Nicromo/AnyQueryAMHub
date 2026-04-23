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
from fastapi.responses import JSONResponse
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
    user, _reason = resolve_user_with_reason(db, request, auth_token_cookie)
    return user


def resolve_user_with_reason(
    db: Session, request, auth_token_cookie: Optional[str]
) -> tuple[Optional[User], str]:
    """
    То же что resolve_user, но дополнительно возвращает причину неудачи:
      - ""                — всё ок (user не None)
      - "token_missing"   — ни cookie, ни Authorization не было
      - "token_invalid"   — JWT не декодируется (повреждён или неправильная подпись)
      - "token_expired"   — JWT истёк
      - "token_unknown"   — amh_* токен не найден (не существует / отозван)
      - "user_not_found"  — токен валидный, но пользователь удалён или деактивирован
      - "token_wrong_format" — Bearer-значение пустое или не похоже ни на JWT, ни на amh_*
    Расширение/клиент на основе reason может показать осмысленное сообщение.
    """
    from jose import jwt as _jwt  # type: ignore
    from jose.exceptions import ExpiredSignatureError, JWTError  # type: ignore
    from auth import SECRET_KEY, ALGORITHM

    def _try_jwt(tok: str) -> tuple[Optional[User], str]:
        if not tok:
            return None, "token_missing"
        try:
            payload = _jwt.decode(tok, SECRET_KEY, algorithms=[ALGORITHM])
        except ExpiredSignatureError:
            return None, "token_expired"
        except JWTError:
            return None, "token_invalid"
        except Exception:
            return None, "token_invalid"
        sub = payload.get("sub")
        if sub is None:
            return None, "token_invalid"
        u = db.query(User).filter(User.id == int(sub)).first()
        if not u or not u.is_active:
            return None, "user_not_found"
        return u, ""

    # 1) Cookie JWT — приоритет для веб-UI
    if auth_token_cookie:
        u, reason = _try_jwt(auth_token_cookie)
        if u:
            return u, ""
        # Если cookie была, но сломана — запомним reason, но дадим шанс Bearer
        cookie_reason = reason
    else:
        cookie_reason = "token_missing"

    # 2) Authorization: Bearer <token> — расширение / API-клиенты
    if request is not None:
        auth_header = request.headers.get("Authorization", "") if hasattr(request, "headers") else ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            if not token:
                return None, "token_wrong_format"
            if token.startswith(TOKEN_PREFIX):
                u = find_user_by_api_token(db, token)
                if u:
                    if not u.is_active:
                        return None, "user_not_found"
                    return u, ""
                return None, "token_unknown"
            # Bearer, но не amh_ — пробуем как JWT
            u, reason = _try_jwt(token)
            if u:
                return u, ""
            return None, reason or "token_invalid"
        elif auth_header:
            # Заголовок есть, но формат не "Bearer ..."
            return None, "token_wrong_format"

    # Ни cookie, ни Bearer не сработали — вернём причину cookie (или token_missing)
    return None, cookie_reason


_REASON_MESSAGES = {
    "token_missing":       "Токен AM Hub не найден — открой настройки расширения и вставь его",
    "token_invalid":       "Токен AM Hub повреждён или неверный — перегенерируй его в хабе и замени в настройках",
    "token_expired":       "Токен AM Hub истёк — создай новый в хабе и замени в настройках",
    "token_unknown":       "Токен AM Hub не найден в системе — возможно он был отозван, создай новый",
    "token_wrong_format":  "Заголовок Authorization неверного формата — ожидается «Bearer amh_…» или JWT",
    "user_not_found":      "Пользователь, которому принадлежит токен, деактивирован — обратись к админу",
}


def require_extension_user(db: Session, request, auth_token_cookie: Optional[str]) -> User:
    """
    Как resolve_user, но сразу бросает HTTPException 401 со структурированным
    detail={"code": <reason>, "message": <human-friendly>}.
    Используется в эндпоинтах, которые работают и с веб-UI, и с расширением.
    Имя намеренно отличается от deps.require_user — у той другая сигнатура.
    """
    user, reason = resolve_user_with_reason(db, request, auth_token_cookie)
    if user:
        return user
    raise HTTPException(
        status_code=401,
        detail={
            "code": reason or "token_invalid",
            "message": _REASON_MESSAGES.get(reason, "Неверный токен AM Hub"),
        },
    )


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


@router.get("/api/me/integrations")
async def api_me_integrations(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Статус подключённых внешних интеграций для текущего пользователя.

    Возвращает bool-флаги: есть ли у юзера сохранённые токены/креды для
    Ktalk, Tbank Time, Merchrules, Airtable и привязка Telegram.

    Фронту этого достаточно, чтобы показать «Подключено / Войти».
    """
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401)
    s = user.settings or {}
    kt  = s.get("ktalk") or {}
    tb  = s.get("tbank_time") or {}
    mr  = s.get("merchrules") or {}
    at  = s.get("airtable") or {}
    result = {
        "ktalk":      bool(kt.get("access_token")),
        "tbank_time": bool(tb.get("mmauthtoken") or tb.get("session_cookie") or tb.get("access_token")),
        "merchrules": bool(mr.get("login") or mr.get("username")),
        "airtable":   bool(at.get("pat") or at.get("token") or at.get("api_key")),
        "telegram":   bool(getattr(user, "telegram_id", None)),
    }
    return JSONResponse(
        content=result,
        headers={"Cache-Control": "private, max-age=60"},  # 1 минута
    )
