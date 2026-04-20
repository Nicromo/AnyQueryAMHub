"""Admin-only endpoints для per-manager бэкапов."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from backups import (
    BACKUP_DIR,
    backup_manager,
    backup_all_managers,
    list_backups,
)
from routers.api_tokens import resolve_user

router = APIRouter()

_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _require_admin(db: Session, request: Request, auth_token: Optional[str]):
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _safe_filename(filename: str) -> str:
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not _FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filename


@router.post("/api/admin/backups/run")
async def run_backup(
    request: Request,
    manager_email: str = "",
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Если manager_email пустой — бэкапим всех активных менеджеров с клиентами."""
    _require_admin(db, request, auth_token)
    email = (manager_email or "").strip()
    if email:
        path = backup_manager(db, email)
        return {"ok": True, "files": [path.name]}
    paths = backup_all_managers(db)
    return {"ok": True, "files": [p.name for p in paths], "count": len(paths)}


@router.get("/api/admin/backups/list")
async def list_backups_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_admin(db, request, auth_token)
    return {"items": list_backups()}


@router.get("/api/admin/backups/download/{filename}")
async def download_backup(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_admin(db, request, auth_token)
    safe = _safe_filename(filename)
    path = BACKUP_DIR / safe
    try:
        resolved = path.resolve()
        resolved.relative_to(BACKUP_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(
        str(resolved),
        media_type="application/gzip",
        filename=safe,
    )


@router.delete("/api/admin/backups/{filename}")
async def delete_backup(
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_admin(db, request, auth_token)
    safe = _safe_filename(filename)
    path = BACKUP_DIR / safe
    try:
        resolved = path.resolve()
        resolved.relative_to(BACKUP_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    try:
        resolved.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return JSONResponse({"ok": True})
