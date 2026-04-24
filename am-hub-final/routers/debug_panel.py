"""Debug panel API — sync logs, integration health, live API ping. Admin-only."""
import os
import logging
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Cookie, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from database import get_db
from auth import decode_access_token
from models import User, SyncLog, AuditLog

logger = logging.getLogger(__name__)
router = APIRouter()

INTEGRATIONS = {
    "merchrules": {
        "label": "Merchrules",
        "env": ["MERCHRULES_LOGIN", "MERCHRULES_API_URL"],
        "test_url": "/api/integrations/test/merchrules",
        "direction": "bidirectional",
        "description": "Клиенты, задачи, встречи, тикеты",
    },
    "airtable": {
        "label": "Airtable",
        "env": ["AIRTABLE_TOKEN", "AIRTABLE_BASE_ID"],
        "test_url": "/api/integrations/test/airtable",
        "direction": "bidirectional",
        "description": "Клиенты, оплаты",
    },
    "ktalk": {
        "label": "KTalk",
        "env": ["KTALK_URL"],
        "test_url": "/api/integrations/test/ktalk",
        "direction": "outbound",
        "description": "Мессенджер — уведомления, фолоуапы",
    },
    "tbank": {
        "label": "KTime (TBank)",
        "env": ["TBANK_URL"],
        "test_url": "/api/integrations/test/tbank",
        "direction": "inbound",
        "description": "Тикеты поддержки",
    },
    "google_sheets": {
        "label": "Google Sheets",
        "env": ["GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SPREADSHEET_ID"],
        "test_url": None,
        "direction": "bidirectional",
        "description": "Чекапы — запись статусов",
    },
    "telegram": {
        "label": "Telegram Bot",
        "env": ["TG_BOT_TOKEN", "TG_NOTIFY_CHAT_ID"],
        "test_url": None,
        "direction": "outbound",
        "description": "Уведомления, рассылки, фолоуапы",
    },
    "jira": {
        "label": "Jira",
        "env": ["JIRA_URL", "JIRA_TOKEN"],
        "test_url": "/api/integrations/test/jira",
        "direction": "inbound",
        "description": "Задачи и баги по клиентам",
    },
    "google_drive": {
        "label": "Google Drive",
        "env": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "test_url": None,
        "direction": "inbound",
        "description": "Файлы по клиентам (OAuth2)",
    },
    "groq": {
        "label": "Groq AI",
        "env": ["GROQ_API_KEY"],
        "test_url": None,
        "direction": "outbound",
        "description": "AI-ассистент, брифинги, контекст",
    },
}


def _require_admin(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    if user.role not in ("admin", "head"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.get("/api/debug/overview")
def debug_overview(
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _require_admin(auth_token, db)

    # Last sync log per integration
    subq = (
        db.query(SyncLog.integration, func.max(SyncLog.id).label("max_id"))
        .group_by(SyncLog.integration)
        .subquery()
    )
    last_logs = (
        db.query(SyncLog)
        .join(subq, SyncLog.id == subq.c.max_id)
        .all()
    )
    last_by_integration = {log.integration: log for log in last_logs}

    # Error counts last 7 days
    week_ago = datetime.utcnow() - timedelta(days=7)
    error_counts = dict(
        db.query(SyncLog.integration, func.count(SyncLog.id))
        .filter(SyncLog.status == "error", SyncLog.started_at >= week_ago)
        .group_by(SyncLog.integration)
        .all()
    )

    # Build integration status list
    integrations = []
    for key, meta in INTEGRATIONS.items():
        configured = all(os.environ.get(e) for e in meta["env"])
        last_log = last_by_integration.get(key)
        integrations.append({
            "key": key,
            "label": meta["label"],
            "description": meta["description"],
            "direction": meta["direction"],
            "configured": configured,
            "missing_env": [e for e in meta["env"] if not os.environ.get(e)] if not configured else [],
            "test_url": meta["test_url"],
            "last_sync": {
                "status": last_log.status,
                "message": last_log.message or "",
                "records": last_log.records_processed,
                "errors": last_log.errors_count,
                "started_at": last_log.started_at.isoformat() if last_log.started_at else None,
                "completed_at": last_log.completed_at.isoformat() if last_log.completed_at else None,
            } if last_log else None,
            "errors_7d": error_counts.get(key, 0),
        })

    # Recent errors (last 20)
    recent_errors = (
        db.query(SyncLog)
        .filter(SyncLog.status == "error")
        .order_by(desc(SyncLog.started_at))
        .limit(20)
        .all()
    )

    # Sync stats last 24h
    day_ago = datetime.utcnow() - timedelta(hours=24)
    total_24h = db.query(func.count(SyncLog.id)).filter(SyncLog.started_at >= day_ago).scalar() or 0
    errors_24h = db.query(func.count(SyncLog.id)).filter(
        SyncLog.started_at >= day_ago, SyncLog.status == "error"
    ).scalar() or 0
    records_24h = db.query(func.sum(SyncLog.records_processed)).filter(
        SyncLog.started_at >= day_ago
    ).scalar() or 0

    return {
        "integrations": integrations,
        "recent_errors": [_log_dict(e) for e in recent_errors],
        "stats_24h": {
            "total_syncs": total_24h,
            "errors": errors_24h,
            "records_processed": int(records_24h),
        },
    }


@router.get("/api/debug/logs")
def debug_logs(
    integration: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _require_admin(auth_token, db)

    q = db.query(SyncLog).order_by(desc(SyncLog.started_at))
    if integration:
        q = q.filter(SyncLog.integration == integration)
    if status:
        q = q.filter(SyncLog.status == status)

    total = q.count()
    logs = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "logs": [_log_dict(log) for log in logs],
    }


@router.get("/api/debug/audit")
def debug_audit(
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    auth_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _require_admin(auth_token, db)

    q = db.query(AuditLog).order_by(desc(AuditLog.created_at))
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)

    logs = q.limit(limit).all()
    return {"logs": [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]}


def _log_dict(log: SyncLog) -> dict:
    duration_ms = None
    if log.started_at and log.completed_at:
        duration_ms = int((log.completed_at - log.started_at).total_seconds() * 1000)
    return {
        "id": log.id,
        "integration": log.integration,
        "resource_type": log.resource_type or "",
        "action": log.action or "",
        "status": log.status,
        "message": log.message or "",
        "records_processed": log.records_processed or 0,
        "errors_count": log.errors_count or 0,
        "duration_ms": duration_ms,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        "sync_data": log.sync_data or {},
    }
