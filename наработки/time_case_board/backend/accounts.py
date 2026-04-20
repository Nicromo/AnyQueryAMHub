from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from backend.config import settings

ACTIVE_ACCOUNT_FILE = "active_time_account.json"
"""Файл в data_dir: локально на машине, не в git."""


def data_dir() -> Path:
    return Path(settings.data_dir)


def active_account_path() -> Path:
    return data_dir() / ACTIVE_ACCOUNT_FILE


def sanitize_account_id(account_id: str) -> str:
    s = (account_id or "").strip()
    if not s:
        return "unknown"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", s)[:200]


def board_db_path(account_id: str) -> Path:
    return data_dir() / f"board_{sanitize_account_id(account_id)}.sqlite"


def legacy_db_path() -> Path:
    return Path(settings.database_path)


def read_active_account_id() -> str | None:
    p = active_account_path()
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            aid = raw.get("account_id")
            if isinstance(aid, str) and aid.strip():
                return aid.strip()
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def write_active_account_id(account_id: str, username: str | None = None) -> None:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"account_id": account_id.strip()}
    if username:
        payload["username"] = username
    active_account_path().write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")


def clear_active_account_file() -> None:
    p = active_account_path()
    if p.is_file():
        try:
            p.unlink()
        except OSError:
            pass


def _try_migrate_legacy_db() -> str | None:
    """Переносит data/time_case_board.db → board_<user_id>.sqlite при первом запуске."""
    legacy = legacy_db_path()
    if not legacy.is_file():
        return None
    if read_active_account_id():
        return None
    eng = None
    db = None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.database import OAuthToken

        eng = create_engine(f"sqlite:///{legacy}", connect_args={"check_same_thread": False})
        Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)
        db = Session()
        row = db.query(OAuthToken).order_by(OAuthToken.id).first()
        token = (row.access_token if row else "") or ""
        if not token.strip():
            return None
        from backend.time_client import TimeClient

        me = TimeClient(token).get_me()
        uid = str(me.get("id") or "").strip()
        if not uid:
            return None
        un = (me.get("username") or "") or None
        new_path = board_db_path(uid)
        if new_path.exists():
            write_active_account_id(uid, un)
            return uid
        shutil.move(str(legacy), str(new_path))
        from backend.database import clear_engine_cache

        clear_engine_cache()
        write_active_account_id(uid, un)
        return uid
    except Exception:
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        if eng is not None:
            try:
                eng.dispose()
            except Exception:
                pass


_pat_me_cache: dict[str, dict[str, Any]] = {}


def clear_pat_me_cache() -> None:
    _pat_me_cache.clear()


def get_me_for_personal_token() -> dict[str, Any] | None:
    pat = (settings.time_personal_access_token or "").strip()
    if not pat:
        return None
    if pat in _pat_me_cache:
        return _pat_me_cache[pat]
    from backend.time_client import TimeClient

    me = TimeClient(pat).get_me()
    _pat_me_cache[pat] = me
    return me


def resolve_active_board_account_id() -> str | None:
    """
    PAT в env имеет приоритет: доска и данные привязаны к user id из /users/me.
    Иначе — active_time_account.json или миграция со старого time_case_board.db.
    """
    me = get_me_for_personal_token()
    if me:
        uid = str(me.get("id") or "").strip()
        if uid:
            if read_active_account_id() != uid:
                write_active_account_id(uid, (me.get("username") or "") or None)
            return uid

    migrated = _try_migrate_legacy_db()
    if migrated:
        return migrated

    active = read_active_account_id()
    if active:
        return active
    return None


def list_local_board_accounts() -> list[dict[str, Any]]:
    """Учётки с локальным файлом доски (для переключателя)."""
    d = data_dir()
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("board_*.sqlite")):
        stem = p.stem  # board_xxx
        from_stem = stem[6:] if stem.startswith("board_") else stem
        try:
            from sqlalchemy.orm import sessionmaker

            from backend.database import OAuthToken, get_engine

            eng = get_engine(p)
            Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)
            s = Session()
            try:
                row = s.query(OAuthToken).order_by(OAuthToken.id).first()
                uid = (row.user_id if row else "") or ""
                un = (row.username if row else "") or ""
            finally:
                s.close()
        except Exception:
            uid, un = "", ""
        out.append(
            {
                "account_id": uid or from_stem,
                "username": un or None,
                "file": p.name,
            }
        )
    legacy = legacy_db_path()
    if legacy.is_file() and not any(a["file"] == legacy.name for a in out):
        out.append(
            {
                "account_id": "__legacy__",
                "username": None,
                "file": legacy.name,
                "legacy": True,
            }
        )
    return out
