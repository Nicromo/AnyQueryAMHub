"""
Per-client onboarding: 10-step templated flow.

Менеджер жмёт «Запустить онбординг» на карточке клиента, далее scheduler
раз в 3-4 дня создаёт задачу «Отправить сообщение по онбордингу #N».
Менеджер копирует текст шаблона из модалки, отправляет клиенту в TG руками
и нажимает «Отправлено» — current_step += 1, фиксируется PartnerLog.
"""
from datetime import datetime, date, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Client, ClientOnboardingProgress, OnboardingTemplate, Task, User,
    CHECKUP_INTERVALS,
)
from auth import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter()

_STEP_GAP_DAYS = 3  # 10 шагов × ~3 дня ≈ 5 недель


def _user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    u = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not u:
        raise HTTPException(status_code=401)
    return u


def _template_body_rendered(body: str, client: Client, user: User) -> str:
    manager_name = (f"{user.first_name or ''} {user.last_name or ''}".strip()
                    or (user.email.split("@")[0] if user.email else "менеджер"))
    checkup_days = CHECKUP_INTERVALS.get(client.segment or "", 60)
    return (body
            .replace("{client_name}", client.name or "")
            .replace("{manager_name}", manager_name)
            .replace("{checkup_days}", str(checkup_days)))


@router.post("/api/clients/{client_id}/onboarding/start")
async def onboarding_start(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404, "client not found")

    existing = (db.query(ClientOnboardingProgress)
                  .filter(ClientOnboardingProgress.client_id == client_id)
                  .first())
    if existing and not existing.completed_at:
        return JSONResponse({"status": "already_active",
                             "progress_id": existing.id,
                             "current_step": existing.current_step})

    if existing and existing.completed_at:
        # перезапуск завершённого — сбрасываем
        existing.started_at = datetime.utcnow()
        existing.started_by = u.email
        existing.current_step = 0
        existing.next_send_date = date.today()
        existing.completed_at = None
        prog = existing
    else:
        prog = ClientOnboardingProgress(
            client_id=client_id,
            started_by=u.email,
            current_step=0,
            next_send_date=date.today(),
        )
        db.add(prog)
    db.commit()
    db.refresh(prog)
    return {"status": "started", "progress_id": prog.id, "current_step": prog.current_step}


@router.get("/api/clients/{client_id}/onboarding")
async def onboarding_status(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)

    prog = (db.query(ClientOnboardingProgress)
              .filter(ClientOnboardingProgress.client_id == client_id)
              .first())
    if not prog:
        return {"active": False}

    next_step = prog.current_step + 1
    tpl = (db.query(OnboardingTemplate)
             .filter(OnboardingTemplate.step == next_step)
             .first()) if next_step <= 10 else None

    open_task = None
    if next_step <= 10:
        open_task = (db.query(Task)
                       .filter(Task.client_id == client_id,
                               Task.task_type == "onboarding_message",
                               Task.status != "done")
                       .order_by(Task.created_at.desc())
                       .first())

    return {
        "active": prog.completed_at is None,
        "progress_id": prog.id,
        "current_step": prog.current_step,
        "next_step": next_step if next_step <= 10 else None,
        "next_send_date": prog.next_send_date.isoformat() if prog.next_send_date else None,
        "completed_at": prog.completed_at.isoformat() if prog.completed_at else None,
        "current_template": {
            "step": tpl.step,
            "title": tpl.title,
            "body": _template_body_rendered(tpl.body, c, u),
        } if tpl else None,
        "open_task_id": open_task.id if open_task else None,
    }


@router.post("/api/clients/{client_id}/onboarding/mark-sent")
async def onboarding_mark_sent(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(404)

    prog = (db.query(ClientOnboardingProgress)
              .filter(ClientOnboardingProgress.client_id == client_id)
              .first())
    if not prog or prog.completed_at:
        raise HTTPException(400, "onboarding not active")
    if prog.current_step >= 10:
        raise HTTPException(400, "already finished")

    sent_step = prog.current_step + 1
    prog.current_step = sent_step

    # Закрываем открытую задачу-отправку для этого шага (если есть)
    open_task = (db.query(Task)
                   .filter(Task.client_id == client_id,
                           Task.task_type == "onboarding_message",
                           Task.status != "done")
                   .order_by(Task.created_at.desc())
                   .first())
    if open_task:
        open_task.status = "done"
        open_task.completed_at = datetime.utcnow()

    if sent_step >= 10:
        prog.completed_at = datetime.utcnow()
        prog.next_send_date = None
    else:
        prog.next_send_date = date.today() + timedelta(days=_STEP_GAP_DAYS)

    # PartnerLog
    try:
        from routers.partner_logs import log_event
        log_event(db, client_id=client_id,
                  event_type=f"onboarding_msg_{sent_step}_sent",
                  title=f"Онбординг #{sent_step} отправлен",
                  created_by=u.email,
                  source="onboarding")
    except Exception as e:
        logger.warning(f"partner_log skipped: {e}")

    db.commit()
    return {"status": "ok", "current_step": prog.current_step,
            "completed": prog.completed_at is not None}


@router.get("/api/onboarding/templates")
async def onboarding_templates_list(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    rows = db.query(OnboardingTemplate).order_by(OnboardingTemplate.step).all()
    return [{"step": r.step, "title": r.title, "body": r.body,
             "updated_at": r.updated_at.isoformat() if r.updated_at else None}
            for r in rows]


@router.put("/api/onboarding/templates/{step}")
async def onboarding_templates_update(
    step: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    u = _user(auth_token, db)
    if u.role != "admin":
        raise HTTPException(403, "admin only")
    if step < 1 or step > 10:
        raise HTTPException(400, "step must be 1..10")
    data = await request.json()
    tpl = (db.query(OnboardingTemplate)
             .filter(OnboardingTemplate.step == step)
             .first())
    if not tpl:
        raise HTTPException(404)
    if "title" in data:
        tpl.title = str(data["title"])
    if "body" in data:
        tpl.body = str(data["body"])
    tpl.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}
