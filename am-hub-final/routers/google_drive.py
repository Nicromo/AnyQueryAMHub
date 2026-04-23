"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional
from datetime import datetime
import os
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database import get_db
from models import Client, User, GDriveFile
from auth import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


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


def _gdrive_flow():
    from google_auth_oauthlib.flow import Flow
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")
    redirect_uri = _env("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/gdrive/callback")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        redirect_uri=redirect_uri,
    )
    return flow


def _gdrive_service(credentials_dict: dict):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=credentials_dict.get("token"),
        refresh_token=credentials_dict.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
    )
    return build("drive", "v3", credentials=creds)


def _file_dict(f: GDriveFile) -> dict:
    return {
        "id": f.id,
        "client_id": f.client_id,
        "gdrive_id": f.gdrive_id,
        "name": f.name,
        "mime_type": f.mime_type,
        "web_view_url": f.web_view_url,
        "web_content_url": f.web_content_url,
        "file_size": f.file_size,
        "modified_at": f.modified_at.isoformat() if f.modified_at else None,
        "linked_by": f.linked_by,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@router.get("/api/gdrive/auth")
async def api_gdrive_auth(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _require_user(auth_token, db)
    if not _env("GOOGLE_CLIENT_ID"):
        raise HTTPException(status_code=400, detail="Google OAuth not configured")
    try:
        flow = _gdrive_flow()
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return {"authorization_url": auth_url}
    except Exception as e:
        logger.error(f"gdrive auth error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/gdrive/callback")
async def api_gdrive_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _require_user(auth_token, db)
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    try:
        flow = _gdrive_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        settings = dict(user.settings or {})
        settings["google"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "scopes": list(creds.scopes) if creds.scopes else [],
        }
        user.settings = settings
        flag_modified(user, "settings")
        db.commit()
        return {"ok": True, "message": "Google Drive connected"}
    except Exception as e:
        logger.error(f"gdrive callback error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/gdrive/files")
async def api_gdrive_files(
    client_id: Optional[int] = Query(None),
    query: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _require_user(auth_token, db)
    settings = user.settings or {}
    google_creds = settings.get("google")
    if not google_creds or not google_creds.get("token"):
        raise HTTPException(status_code=400, detail="Google Drive not connected")
    try:
        service = _gdrive_service(google_creds)
        q_parts = ["trashed=false"]
        if query:
            q_parts.append(f"name contains '{query}'")
        elif client_id:
            client = db.query(Client).filter(Client.id == client_id).first()
            if client:
                q_parts.append(f"name contains '{client.name}'")
        q_str = " and ".join(q_parts)
        result = service.files().list(
            q=q_str,
            pageSize=50,
            fields="files(id,name,mimeType,webViewLink,webContentLink,size,modifiedTime)",
        ).execute()
        files = [
            {
                "gdrive_id": f["id"],
                "name": f["name"],
                "mime_type": f.get("mimeType"),
                "web_view_url": f.get("webViewLink"),
                "web_content_url": f.get("webContentLink"),
                "file_size": int(f["size"]) if f.get("size") else None,
                "modified_at": f.get("modifiedTime"),
            }
            for f in result.get("files", [])
        ]
        return {"files": files}
    except Exception as e:
        logger.error(f"gdrive list error: {e}")
        return {"error": str(e), "files": []}


@router.post("/api/clients/{client_id}/gdrive/link")
async def api_gdrive_link_file(
    client_id: int,
    request: Request,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _require_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    data = await request.json()
    gdrive_id = data.get("gdrive_id")
    if not gdrive_id:
        raise HTTPException(status_code=400, detail="gdrive_id required")
    existing = db.query(GDriveFile).filter(
        GDriveFile.client_id == client_id,
        GDriveFile.gdrive_id == gdrive_id,
    ).first()
    if existing:
        return {"ok": True, "id": existing.id, "already_linked": True}

    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None

    f = GDriveFile(
        client_id=client_id,
        linked_by=user.email,
        gdrive_id=gdrive_id,
        name=data.get("name", gdrive_id),
        mime_type=data.get("mime_type"),
        web_view_url=data.get("web_view_url"),
        web_content_url=data.get("web_content_url"),
        file_size=data.get("file_size"),
        modified_at=_parse_dt(data.get("modified_at")),
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"ok": True, "id": f.id}


@router.get("/api/clients/{client_id}/gdrive")
async def api_client_gdrive_files(
    client_id: int,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _require_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    files = (
        db.query(GDriveFile)
        .filter(GDriveFile.client_id == client_id)
        .order_by(GDriveFile.created_at.desc())
        .all()
    )
    return {"files": [_file_dict(f) for f in files]}


@router.delete("/api/clients/{client_id}/gdrive/{file_id}")
async def api_gdrive_unlink_file(
    client_id: int,
    file_id: int,
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _require_user(auth_token, db)
    f = db.query(GDriveFile).filter(
        GDriveFile.id == file_id,
        GDriveFile.client_id == client_id,
    ).first()
    if not f:
        raise HTTPException(status_code=404)
    if user.role != "admin" and f.linked_by and f.linked_by != user.email:
        raise HTTPException(status_code=403)
    db.delete(f)
    db.commit()
    return {"ok": True}
