from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import OAuthToken
from backend.oauth_time import refresh_access_token


def _now_ms() -> int:
    return int(time.time() * 1000)


def get_stored_token_row(db: Session) -> OAuthToken | None:
    return db.query(OAuthToken).order_by(OAuthToken.id).first()


def save_token_response(db: Session, data: dict[str, Any]) -> None:
    row = get_stored_token_row(db)
    if not row:
        row = OAuthToken()
        db.add(row)
    row.access_token = data.get("access_token") or ""
    if data.get("refresh_token"):
        row.refresh_token = data["refresh_token"]
    exp = data.get("expires_in")
    if isinstance(exp, (int, float)):
        row.expires_at_ms = _now_ms() + int(exp * 1000)
    db.commit()


def get_access_token_for_api(db: Session) -> str:
    pat = (settings.time_personal_access_token or "").strip()
    if pat:
        return pat
    row = get_stored_token_row(db)
    if not row or not row.access_token:
        raise PermissionError("not_authenticated")
    if row.expires_at_ms and row.expires_at_ms > _now_ms() + 60_000:
        return row.access_token
    if row.refresh_token and settings.oauth_client_id and settings.oauth_client_secret:
        data = refresh_access_token(row.refresh_token)
        save_token_response(db, data)
        return data.get("access_token") or ""
    if row.access_token:
        return row.access_token
    raise PermissionError("not_authenticated")
