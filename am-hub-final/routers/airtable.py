"""
Airtable trigger endpoint — ручной запуск синка клиентов (admin only).
Основной sync живёт в airtable_sync.sync_clients_from_airtable и также
запускается по расписанию через scheduler.job_sync_airtable_clients.
"""
import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Cookie, Request
from sqlalchemy.orm import Session

from database import get_db
from models import User, SyncLog
from auth import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_admin(db: Session, auth_token: Optional[str]) -> User:
    if not auth_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.post("/api/airtable/sync")
async def trigger_airtable_sync(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Ручной триггер полного синка клиентов из Airtable (admin only).

    Возвращает {created, updated, skipped, errors, field_map}.
    """
    user = _require_admin(db, auth_token)

    try:
        body = await request.json()
    except Exception:
        body = {}

    token = (
        body.get("token")
        or os.environ.get("AIRTABLE_TOKEN")
        or os.environ.get("AIRTABLE_PAT", "")
    )
    base_id = body.get("base_id") or os.environ.get("AIRTABLE_BASE_ID", "")
    table_id = body.get("table_id") or os.environ.get("AIRTABLE_TABLE_ID", "")
    view_id = body.get("view_id") or os.environ.get("AIRTABLE_VIEW_ID", "")

    if not token:
        return {"ok": False, "error": "AIRTABLE_TOKEN не задан в окружении или body",
                "created": 0, "updated": 0, "skipped": 0, "errors": []}

    from airtable_sync import sync_clients_from_airtable

    sync_log = SyncLog(
        integration="airtable",
        resource_type="clients",
        action="manual_sync",
        status="in_progress",
        sync_data={"triggered_by": user.email},
    )
    db.add(sync_log)
    db.commit()

    try:
        report = await sync_clients_from_airtable(
            db=db,
            token=token,
            base_id=base_id or None,
            view_id=view_id,
            table_id=table_id or "",
            default_manager_email=user.email,
        )
        sync_log.status = "success" if report.get("ok") else "error"
        sync_log.records_processed = report.get("created", 0) + report.get("updated", 0)
        sync_log.message = report.get("error") or ""
        db.add(sync_log)
        db.commit()
        logger.info(
            "Airtable manual sync by %s: created=%s updated=%s skipped=%s",
            user.email, report.get("created"), report.get("updated"), report.get("skipped"),
        )
        return report
    except Exception as e:
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
        db.add(sync_log)
        db.commit()
        logger.exception("Airtable manual sync failed")
        return {"ok": False, "error": str(e),
                "created": 0, "updated": 0, "skipped": 0, "errors": [str(e)]}


@router.get("/api/airtable/status")
async def airtable_status(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Конфигурация Airtable (без секретов) + последний sync-лог."""
    user = _require_admin(db, auth_token)
    last = (
        db.query(SyncLog)
        .filter(SyncLog.integration == "airtable")
        .order_by(SyncLog.started_at.desc())
        .first()
    )
    return {
        "configured": bool(os.environ.get("AIRTABLE_TOKEN") or os.environ.get("AIRTABLE_PAT")),
        "base_id": os.environ.get("AIRTABLE_BASE_ID", ""),
        "table_id": os.environ.get("AIRTABLE_TABLE_ID", ""),
        "view_id": os.environ.get("AIRTABLE_VIEW_ID", ""),
        "last_sync": {
            "status": last.status if last else None,
            "records_processed": last.records_processed if last else 0,
            "message": last.message if last else None,
            "started_at": last.started_at.isoformat() if (last and last.started_at) else None,
        } if last else None,
    }
