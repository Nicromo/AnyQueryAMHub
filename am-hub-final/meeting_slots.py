"""
meeting_slots.py — Автопланирование слотов для встреч AM-менеджера.

Логика по типам встреч:
  meeting   → prep (за 30 мин до) + followup (через 30 мин после)
  checkup   → prep (за 60 мин до) + followup (через 60 мин после)
  onboarding/kickoff → prep (за 60 мин до) + followup (через 30 мин) + серия касаний
  qbr       → prep (за 5 ч до — сбор данных) + followup (через 60 мин после)
  upsell/downsell → followup с заполнением Airtable

Для каждой встречи создаётся набор Task со статусом plan и точным due_date.
Если задача с таким meeting_id + source уже есть — не дублируем.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import Meeting, Task, Client

logger = logging.getLogger(__name__)

# ── Тайминги по типу встречи ────────────────────────────────────────────────

SLOT_CONFIG = {
    # meeting_type: {
    #   "prep_before_min": минут до встречи,
    #   "prep_duration_min": длительность подготовки,
    #   "followup_after_min": минут после встречи до дедлайна фолоуапа,
    #   "extra_tasks": список доп. задач [(title, offset_hours_after)]
    # }
    "meeting": {
        "prep_before_min": 30,
        "prep_duration_min": 20,
        "followup_after_min": 30,
        "extra_tasks": [],
    },
    "checkup": {
        "prep_before_min": 60,
        "prep_duration_min": 30,
        "followup_after_min": 60,
        "extra_tasks": [],
    },
    "kickoff": {
        "prep_before_min": 60,
        "prep_duration_min": 60,
        "followup_after_min": 30,
        "extra_tasks": [
            ("Отправить касание партнёру (день 3)", 72),
            ("Отправить касание партнёру (день 7)", 168),
            ("Отправить касание партнёру (день 14)", 336),
        ],
    },
    "onboarding": {
        "prep_before_min": 60,
        "prep_duration_min": 60,
        "followup_after_min": 30,
        "extra_tasks": [
            ("Отправить касание партнёру (день 3)", 72),
            ("Отправить касание партнёру (день 7)", 168),
            ("Отправить касание партнёру (день 14)", 336),
        ],
    },
    "qbr": {
        "prep_before_min": 300,  # 5 часов — сбор аналитики
        "prep_duration_min": 180,
        "followup_after_min": 60,
        "extra_tasks": [],
    },
    "upsell": {
        "prep_before_min": 30,
        "prep_duration_min": 20,
        "followup_after_min": 60,
        "extra_tasks": [
            ("Заполнить Airtable (апсейл)", 2),
            ("Обновить карточку клиента", 4),
        ],
    },
    "downsell": {
        "prep_before_min": 30,
        "prep_duration_min": 20,
        "followup_after_min": 60,
        "extra_tasks": [
            ("Заполнить Airtable (дауnsейл)", 2),
            ("Обновить карточку клиента", 4),
        ],
    },
    "sync": {
        "prep_before_min": 15,
        "prep_duration_min": 15,
        "followup_after_min": 30,
        "extra_tasks": [],
    },
    "other": {
        "prep_before_min": 30,
        "prep_duration_min": 20,
        "followup_after_min": 30,
        "extra_tasks": [],
    },
}

DEFAULT_MEETING_DURATION_MIN = 60  # если нет end_time в meeting


def _meeting_end(meeting: Meeting) -> datetime:
    """Вернуть время окончания встречи (если нет — start + 60 мин)."""
    # Meeting.date — время начала
    return meeting.date + timedelta(minutes=DEFAULT_MEETING_DURATION_MIN)


def _task_exists(db: Session, meeting_id: int, source_tag: str) -> bool:
    """Проверить, не создана ли задача-слот уже."""
    return db.query(Task).filter(
        Task.created_from_meeting_id == meeting_id,
        Task.source == source_tag,
    ).first() is not None


def create_slots_for_meeting(db: Session, meeting: Meeting) -> list[Task]:
    """
    Создать задачи-слоты (prep + followup + доп.) для встречи.
    Возвращает список созданных задач (пустой, если все уже были).
    """
    if not meeting.date:
        return []

    meeting_type = (meeting.type or "other").lower()
    config = SLOT_CONFIG.get(meeting_type, SLOT_CONFIG["other"])
    client_name = meeting.client.name if meeting.client else f"Клиент #{meeting.client_id}"
    meeting_label = meeting.title or meeting_type.upper()
    meeting_end = _meeting_end(meeting)

    created: list[Task] = []

    # ── Prep ────────────────────────────────────────────────────────────────
    prep_source = f"prep_slot_{meeting.id}"
    if not _task_exists(db, meeting.id, prep_source):
        prep_deadline = meeting.date - timedelta(minutes=config["prep_before_min"])
        prep_task = Task(
            client_id=meeting.client_id,
            created_from_meeting_id=meeting.id,
            title=f"📋 Подготовка: {client_name} — {meeting_label}",
            description=(
                f"Подготовиться до {prep_deadline.strftime('%H:%M')} "
                f"({config['prep_duration_min']} мин). "
                f"Встреча в {meeting.date.strftime('%H:%M')}."
            ),
            status="plan",
            priority="high",
            source=prep_source,
            due_date=prep_deadline,
            task_type="prep",
        )
        db.add(prep_task)
        created.append(prep_task)
        logger.info(f"✅ Created prep slot for meeting {meeting.id} ({client_name})")

    # ── Followup ─────────────────────────────────────────────────────────────
    followup_source = f"followup_slot_{meeting.id}"
    if not _task_exists(db, meeting.id, followup_source):
        followup_deadline = meeting_end + timedelta(minutes=config["followup_after_min"])
        followup_task = Task(
            client_id=meeting.client_id,
            created_from_meeting_id=meeting.id,
            title=f"✍️ Фолоуап: {client_name} — {meeting_label}",
            description=(
                f"Написать фолоуап до {followup_deadline.strftime('%H:%M')}. "
                f"Встреча завершилась в {meeting_end.strftime('%H:%M')}."
            ),
            status="plan",
            priority="high",
            source=followup_source,
            due_date=followup_deadline,
            task_type="followup",
        )
        db.add(followup_task)
        created.append(followup_task)
        logger.info(f"✅ Created followup slot for meeting {meeting.id} ({client_name})")

    # ── Доп. задачи (касания, Airtable и т.д.) ──────────────────────────────
    for i, (task_title, offset_hours) in enumerate(config["extra_tasks"]):
        extra_source = f"extra_slot_{meeting.id}_{i}"
        if not _task_exists(db, meeting.id, extra_source):
            extra_deadline = meeting_end + timedelta(hours=offset_hours)
            extra_task = Task(
                client_id=meeting.client_id,
                created_from_meeting_id=meeting.id,
                title=f"{task_title}: {client_name}",
                description=f"Авто-задача по типу встречи '{meeting_type}'.",
                status="plan",
                priority="medium",
                source=extra_source,
                due_date=extra_deadline,
                task_type="extra",
            )
            db.add(extra_task)
            created.append(extra_task)

    if created:
        db.commit()
        for t in created:
            db.refresh(t)

    return created


def get_day_slots(db: Session, user_email: str, date: datetime) -> list[dict]:
    """
    Получить все слоты (встречи + авто-задачи) за конкретный день.
    Возвращает список событий, отсортированных по времени.
    """
    day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # Встречи за день
    meetings_q = db.query(Meeting).join(
        Client, Meeting.client_id == Client.id, isouter=True
    ).filter(
        Meeting.date >= day_start,
        Meeting.date < day_end,
        Client.manager_email == user_email,
    ).order_by(Meeting.date)

    meetings = meetings_q.all()

    # Задачи-слоты за день (prep/followup с due_date)
    tasks_q = db.query(Task).join(
        Client, Task.client_id == Client.id, isouter=True
    ).filter(
        Task.due_date >= day_start,
        Task.due_date < day_end,
        Task.task_type.in_(["prep", "followup", "extra"]),
        Task.status != "done",
        Client.manager_email == user_email,
    ).order_by(Task.due_date)

    tasks = tasks_q.all()

    slots = []

    for m in meetings:
        meeting_type = m.type or "other"
        color_map = {
            "checkup": "#22c55e",
            "qbr": "#6366f1",
            "kickoff": "#f97316",
            "onboarding": "#f97316",
            "sync": "#3b82f6",
            "upsell": "#10b981",
            "downsell": "#ef4444",
            "meeting": "#64748b",
        }
        slots.append({
            "id": f"meeting-{m.id}",
            "type": "meeting",
            "meeting_type": meeting_type,
            "title": f"{m.client.name + ': ' if m.client else ''}{m.title or meeting_type.upper()}",
            "time": m.date.strftime("%H:%M") if m.date else "",
            "datetime": m.date.isoformat() if m.date else "",
            "client_id": m.client_id,
            "client_name": m.client.name if m.client else "",
            "color": color_map.get(meeting_type, "#64748b"),
            "followup_status": m.followup_status,
            "url": f"/followup/{m.client_id}",
            "duration_min": DEFAULT_MEETING_DURATION_MIN,
        })

    for t in tasks:
        task_type = t.task_type or "prep"
        color_map = {"prep": "#eab308", "followup": "#8b5cf6", "extra": "#06b6d4"}
        icon_map = {"prep": "📋", "followup": "✍️", "extra": "📌"}
        slots.append({
            "id": f"task-{t.id}",
            "type": "slot",
            "slot_type": task_type,
            "title": t.title,
            "time": t.due_date.strftime("%H:%M") if t.due_date else "",
            "datetime": t.due_date.isoformat() if t.due_date else "",
            "client_id": t.client_id,
            "client_name": t.client.name if t.client else "",
            "color": color_map.get(task_type, "#64748b"),
            "icon": icon_map.get(task_type, "📌"),
            "status": t.status,
            "task_id": t.id,
            "url": f"/client/{t.client_id}",
        })

    slots.sort(key=lambda s: s.get("datetime") or "")
    return slots


def process_new_meetings(db: Session, meetings: list[Meeting]) -> int:
    """
    Обработать список новых встреч — создать слоты для каждой.
    Возвращает кол-во созданных задач.
    """
    total = 0
    for meeting in meetings:
        created = create_slots_for_meeting(db, meeting)
        total += len(created)
    return total
