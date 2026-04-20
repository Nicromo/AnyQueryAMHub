"""
tg_notifications.py — единая точка отправки нотификаций менеджеру.

notify_manager(user, kind, payload) формирует текст по шаблону,
шлёт в Telegram через send_telegram (если привязан chat_id),
пишет запись в Notification (UI инбокс). Использует встроенные шаблоны,
чтобы не плодить форматирование в каждом job'e.

Поддерживаемые kind:
  sync_fail        — синк Merchrules/Airtable упал
  task_deadline    — задача с due_date истекает завтра
  meeting_soon     — встреча через <1 час
  checkup_result   — чекап завершён, есть критичные зоны
  qbr_ready        — AI-бриф к QBR готов
  nps_incoming     — пришёл NPS ответ ≤6 (детрактор)
  churn_risk       — health упал ниже threshold
"""
from datetime import datetime
from typing import Any, Optional
import logging

from sqlalchemy.orm import Session

from models import User, Notification

logger = logging.getLogger(__name__)


# ── Шаблоны: (заголовок, формат body, тип в Notification) ───────────────────
_TPL = {
    "sync_fail":      ("⚠️ Синк упал",
                       "Интеграция «{integration}» упала: {error}",
                       "alert"),
    "task_deadline":  ("⏰ Дедлайн завтра",
                       "Задача «{title}» по клиенту {client} — срок {due}",
                       "warning"),
    "meeting_soon":   ("📞 Встреча скоро",
                       "Встреча с {client} в {time} ({type}). Подготовка — {prep_url}",
                       "info"),
    "checkup_result": ("🩺 Чекап готов",
                       "Чекап по {client}: {summary}",
                       "info"),
    "qbr_ready":      ("📊 QBR-бриф готов",
                       "Бриф к QBR с {client} подготовлен — {link}",
                       "info"),
    "nps_incoming":   ("📉 Низкий NPS",
                       "{client} оценил на {score}. Комментарий: {comment}",
                       "alert"),
    "churn_risk":     ("🚨 Churn risk",
                       "{client}: health упал до {health} ({reason})",
                       "alert"),
}


def _format(kind: str, payload: dict) -> tuple:
    tpl = _TPL.get(kind)
    if not tpl:
        return ("Уведомление", str(payload), "info")
    title, body_tpl, kind_type = tpl
    try:
        body = body_tpl.format(**{k: payload.get(k, "—") for k in payload})
    except Exception:
        body = body_tpl
    return (title, body, kind_type)


async def notify_manager(
    db: Session,
    user: Optional[User],
    kind: str,
    payload: dict,
    *,
    related_type: Optional[str] = None,
    related_id: Optional[int] = None,
) -> bool:
    """Шлёт в TG + создаёт Notification. user=None → только лог."""
    if not user:
        logger.warning(f"notify_manager: user is None, kind={kind}")
        return False
    title, body, ntype = _format(kind, payload)

    # 1. Inbox
    try:
        n = Notification(
            user_id=user.id,
            title=title,
            message=body,
            type=ntype,
            related_resource_type=related_type,
            related_resource_id=related_id,
            is_read=False,
            created_at=datetime.utcnow(),
        )
        db.add(n)
        db.flush()
    except Exception as e:
        logger.warning(f"notify_manager inbox write failed: {e}")

    # 2. Telegram (если привязан)
    tg_ok = False
    if user.telegram_id:
        try:
            from scheduler import send_telegram
            tg_text = f"*{title}*\n{body}"
            tg_ok = await send_telegram(int(user.telegram_id), tg_text)
        except Exception as e:
            logger.warning(f"notify_manager tg send failed: {e}")
    return tg_ok


async def notify_by_email(
    db: Session, manager_email: str, kind: str, payload: dict,
    **kwargs,
) -> bool:
    u = db.query(User).filter(User.email == manager_email,
                              User.is_active == True).first()
    return await notify_manager(db, u, kind, payload, **kwargs)
