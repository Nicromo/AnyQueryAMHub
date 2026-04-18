"""
routers/files.py — загрузка/отдача/удаление файлов кабинета менеджера.

Storage: FILES_STORAGE_DIR (default /tmp/amhub_files).
TODO: для продакшена настроить Railway Volume на /data/uploads для персистентности.
"""
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from auth import decode_access_token
from database import get_db
from models import FileUpload, User

STORAGE_DIR = Path(os.getenv("FILES_STORAGE_DIR", "/tmp/amhub_files"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MAX_SIZE = int(os.getenv("FILES_MAX_SIZE", str(25 * 1024 * 1024)))  # 25 MB

router = APIRouter(prefix="/api/files", tags=["files"])


def _user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    u = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not u:
        raise HTTPException(status_code=401, detail="User not found")
    return u


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    client_id: Optional[int] = Form(None),
    category: str = Form("misc"),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_SIZE} bytes)")
    ext = Path(file.filename or "").suffix[:10]
    storage_name = f"{uuid.uuid4().hex}{ext}"
    path = STORAGE_DIR / storage_name
    path.write_bytes(content)
    rec = FileUpload(
        user_id=u.id,
        client_id=client_id,
        filename=file.filename or storage_name,
        storage_path=str(path),
        mime_type=file.content_type,
        size_bytes=len(content),
        category=category,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"id": rec.id, "filename": rec.filename, "size": rec.size_bytes, "url": f"/api/files/{rec.id}"}


@router.get("")
async def list_files(
    client_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    q = db.query(FileUpload).order_by(FileUpload.created_at.desc())
    if (u.role or "") != "admin":
        q = q.filter(FileUpload.user_id == u.id)
    if client_id:
        q = q.filter(FileUpload.client_id == client_id)
    rows = q.limit(max(1, min(200, limit))).all()
    return {
        "files": [{
            "id": r.id,
            "filename": r.filename,
            "mime_type": r.mime_type,
            "size_bytes": r.size_bytes,
            "category": r.category,
            "client_id": r.client_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "url": f"/api/files/{r.id}",
        } for r in rows],
    }


@router.get("/{file_id}")
async def download_file(
    file_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    rec = db.query(FileUpload).filter(FileUpload.id == file_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="File not found")
    if rec.user_id != u.id and (u.role or "") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    if not Path(rec.storage_path).exists():
        raise HTTPException(status_code=410, detail="File gone (storage not persistent?)")
    return FileResponse(rec.storage_path, filename=rec.filename, media_type=rec.mime_type or "application/octet-stream")


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    rec = db.query(FileUpload).filter(FileUpload.id == file_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="File not found")
    if rec.user_id != u.id and (u.role or "") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        Path(rec.storage_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.delete(rec)
    db.commit()
    return {"ok": True}
