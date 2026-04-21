"""
Probe endpoints для мониторинга.

GET /health      — быстрый ping (process alive)
GET /health/deep — глубокая проверка (DB, Redis, scheduler, опц. Merchrules)

Используется в Railway healthcheck и внутренним мониторингом.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter()

START_TIME = datetime.utcnow()


@router.get("/health")
async def health():
    """Simple liveness probe."""
    uptime_s = (datetime.utcnow() - START_TIME).total_seconds()
    return {"status": "ok", "uptime_s": round(uptime_s, 1)}


@router.get("/health/deep")
async def health_deep() -> Dict[str, Any]:
    """Комплексная проверка: DB ping, Redis ping, scheduler alive."""
    checks: Dict[str, Dict[str, Any]] = {}
    overall_ok = True

    # DB
    try:
        from database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            checks["db"] = {"ok": True}
        finally:
            db.close()
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)[:200]}
        overall_ok = False

    # Redis (опционально — только если задан REDIS_URL)
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("UPSTASH_REDIS_URL")
    if redis_url:
        try:
            import redis  # type: ignore
            r = redis.from_url(redis_url, socket_timeout=3)
            pong = r.ping()
            checks["redis"] = {"ok": bool(pong)}
            if not pong:
                overall_ok = False
        except Exception as e:
            checks["redis"] = {"ok": False, "error": str(e)[:200]}
            overall_ok = False
    else:
        checks["redis"] = {"ok": True, "skipped": "no REDIS_URL"}

    # Scheduler
    try:
        from scheduler import _get_scheduler
        sched = _get_scheduler()
        running = bool(sched and sched.running)
        checks["scheduler"] = {"ok": running, "jobs": len(sched.get_jobs()) if running else 0}
        if not running:
            overall_ok = False
    except Exception as e:
        checks["scheduler"] = {"ok": False, "error": str(e)[:200]}
        overall_ok = False

    # Uptime
    uptime_s = (datetime.utcnow() - START_TIME).total_seconds()

    return {
        "status": "ok" if overall_ok else "degraded",
        "uptime_s": round(uptime_s, 1),
        "checks": checks,
    }
