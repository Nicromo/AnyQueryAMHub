"""
churn.py — скоринг риска оттока клиентов.
Пересчитывается еженедельно через scheduler.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict

logger = logging.getLogger(__name__)

CHECKUP_INTERVALS = {"Enterprise": 30, "Enterprise+": 30, "SMB": 90, "SME": 60, "SS": 180, "Partner": 60}


def calculate_churn_score(client, tasks: list, meetings: list) -> Dict:
    """
    Считает риск оттока 0-100 для клиента.
    Факторы:
    - days_no_contact (0-30 очков)
    - health_score (0-25 очков)
    - overdue_tasks (0-20 очков)
    - meeting_frequency (0-15 очков)
    - health_trend (0-10 очков — резервно)
    """
    now = datetime.utcnow()
    score = 0
    factors = {}

    # 1. Дни без контакта
    last_contact = client.last_meeting_date or client.last_checkup
    days_no_contact = (now - last_contact).days if last_contact else 365
    interval = CHECKUP_INTERVALS.get(client.segment or "", 90)
    contact_ratio = min(1.0, days_no_contact / (interval * 1.5))
    contact_pts = round(contact_ratio * 30)
    score += contact_pts
    factors["days_no_contact"] = {"days": days_no_contact, "points": contact_pts}

    # 2. Health score
    health = client.health_score or 50
    health_pts = round((1 - health / 100) * 25)
    score += health_pts
    factors["health_score"] = {"value": health, "points": health_pts}

    # 3. Просроченные задачи
    overdue_tasks = [t for t in tasks if t.get("due_date") and t.get("status") != "done"
                     and datetime.fromisoformat(t["due_date"]) < now]
    overdue_pts = min(20, len(overdue_tasks) * 5)
    score += overdue_pts
    factors["overdue_tasks"] = {"count": len(overdue_tasks), "points": overdue_pts}

    # 4. Частота встреч за последние 90 дней
    recent_meetings = [m for m in meetings
                       if m.get("date") and datetime.fromisoformat(str(m["date"])) > now - timedelta(days=90)]
    meeting_pts = max(0, 15 - len(recent_meetings) * 3)
    score += meeting_pts
    factors["meeting_frequency"] = {"count_90d": len(recent_meetings), "points": meeting_pts}

    # Уровень риска
    if score >= 70:   risk_level = "critical"
    elif score >= 50: risk_level = "high"
    elif score >= 30: risk_level = "medium"
    else:             risk_level = "low"

    return {
        "score": min(100, score),
        "risk_level": risk_level,
        "factors": factors,
    }


async def recalculate_all(db) -> int:
    """Пересчитать churn score для всех клиентов."""
    from models import Client, Task, Meeting, ChurnScore
    from sqlalchemy.orm.attributes import flag_modified

    clients = db.query(Client).all()
    updated = 0

    for client in clients:
        tasks    = [{"due_date": t.due_date.isoformat() if t.due_date else None, "status": t.status}
                    for t in db.query(Task).filter(Task.client_id == client.id).all()]
        meetings = [{"date": m.date.isoformat() if m.date else None}
                    for m in db.query(Meeting).filter(Meeting.client_id == client.id).all()]

        result = calculate_churn_score(client, tasks, meetings)

        cs = db.query(ChurnScore).filter(ChurnScore.client_id == client.id).first()
        if cs:
            cs.score = result["score"]
            cs.risk_level = result["risk_level"]
            cs.factors = result["factors"]
            cs.calculated_at = datetime.utcnow()
            flag_modified(cs, "factors")
        else:
            cs = ChurnScore(
                client_id=client.id,
                score=result["score"],
                risk_level=result["risk_level"],
                factors=result["factors"],
            )
            db.add(cs)
        updated += 1

    db.commit()
    logger.info(f"Churn scores recalculated: {updated} clients")
    return updated
