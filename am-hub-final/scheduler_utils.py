"""
scheduler_utils.py — единственный источник создания автотасок.

Все автотаски (meeting prep/followup, checkup due, QBR sync, QBR prep)
создаются через get_or_create_autotask — она гарантирует дедупликацию по
(client_id, meta->>'rule_key', meta->>'target_date', status != 'done').
"""
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import Task


def _iso_date(d) -> str:
    """Нормализуем target_date к ISO-строке (YYYY-MM-DD)."""
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def get_or_create_autotask(
    db: Session,
    client_id: Optional[int],
    rule_key: str,
    target_date,
    *,
    manager_email: Optional[str] = None,
    title: str,
    task_type: str = "autotask",
    due_date: Optional[datetime] = None,
    meta: Optional[dict] = None,
    priority: str = "medium",
    description: Optional[str] = None,
) -> Task:
    """
    Идемпотентное создание автотаски.

    Дубль определяется по: client_id + meta->>'rule_key' + meta->>'target_date'
    среди задач со status != 'done'. Если такой Task существует — возвращаем его
    без изменений (no-op). Иначе создаём новый Task с заполненным meta.
    """
    target_iso = _iso_date(target_date)
    base_q = db.query(Task).filter(
        Task.meta["rule_key"].astext == rule_key,
        Task.meta["target_date"].astext == target_iso,
        Task.status != "done",
    )
    if client_id is None:
        base_q = base_q.filter(Task.client_id.is_(None))
    else:
        base_q = base_q.filter(Task.client_id == client_id)

    existing = base_q.first()
    if existing:
        return existing

    payload = dict(meta or {})
    payload["rule_key"] = rule_key
    payload["target_date"] = target_iso

    task = Task(
        client_id=client_id,
        title=title,
        description=description,
        status="plan",
        priority=priority,
        source="autotask",
        task_type=task_type,
        due_date=due_date,
        team=manager_email or None,
        meta=payload,
    )
    db.add(task)
    db.flush()
    return task


def cancel_autotasks_by_rule(db: Session, rule_keys: list[str]) -> int:
    """
    Снять открытые автотаски по списку rule_key (status → 'cancelled').
    Используется при отмене/удалении встречи.
    """
    if not rule_keys:
        return 0
    tasks = db.query(Task).filter(
        Task.meta["rule_key"].astext.in_(rule_keys),
        Task.status.notin_(("done", "cancelled")),
    ).all()
    for t in tasks:
        t.status = "cancelled"
    return len(tasks)


def update_autotask_on_reschedule(
    db: Session,
    rule_key: str,
    *,
    new_due_date: Optional[datetime] = None,
    new_title: Optional[str] = None,
    new_target_date=None,
) -> Optional[Task]:
    """
    При переносе встречи обновляем открытую meeting_prep-задачу: due_date + title +
    meta.target_date. Возвращаем задачу (или None, если её нет — значит ещё не
    создали, создастся скедулером на новую дату).
    """
    t = db.query(Task).filter(
        Task.meta["rule_key"].astext == rule_key,
        Task.status.notin_(("done", "cancelled")),
    ).first()
    if not t:
        return None
    if new_due_date is not None:
        t.due_date = new_due_date
    if new_title:
        t.title = new_title
    if new_target_date is not None:
        payload = dict(t.meta or {})
        payload["target_date"] = _iso_date(new_target_date)
        t.meta = payload
        flag_modified(t, "meta")
    return t
