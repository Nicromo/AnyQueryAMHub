"""
design_mappers.py — ORM → design-dict конвертеры для нового UI.

Новый дизайн (папка ДИЗАЙН/) ожидает определённую форму данных
в window.CLIENTS / window.TASKS / window.MEETINGS.
Здесь мы переводим SQLAlchemy-модели в эту форму.

Два места помечены как TODO — это бизнес/UX-решения,
которые лучше принять вам (см. раздел внизу, как запустить).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from models import CHECKUP_INTERVALS, Meeting, Task


# ──────────────────────────────────────────────────────────────
# Настраиваемые пороги (через env, без редеплоя).
# Railway → Variables → задать HEALTH_RISK_MAX / HEALTH_WARN_MAX.
# Дефолты — "жёсткие" (проактивно показывают риски).
# ──────────────────────────────────────────────────────────────
def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return default


# health_score ниже этого → "risk" (красный)
HEALTH_RISK_MAX = _env_float("HEALTH_RISK_MAX", 0.55)
# health_score ниже этого (но ≥ RISK_MAX) → "warn" (жёлтый)
HEALTH_WARN_MAX = _env_float("HEALTH_WARN_MAX", 0.80)
# needs_checkup + давно не было встречи → force "risk"
STALE_MEETING_DAYS = int(_env_float("HEALTH_STALE_MEETING_DAYS", 30))


# ──────────────────────────────────────────────────────────────
# СЕГМЕНТЫ — оставляем как в БД: SS / SMB / SME / ENT (+ SME+/SME-)
# ──────────────────────────────────────────────────────────────
SEGMENT_SHORT: Dict[str, str] = {
    "ENT":  "ENT",
    "SME":  "SME",
    "SME+": "SME+",
    "SME-": "SME-",
    "SMB":  "SMB",
    "SS":   "SS",
}


def segment_label(segment: Optional[str]) -> str:
    """Короткий лейбл сегмента для UI. Неизвестные — показываем as-is или прочерк."""
    if not segment:
        return "—"
    return SEGMENT_SHORT.get(segment, segment)


# ──────────────────────────────────────────────────────────────
# Статус клиента — гибрид health_score + checkup/meeting_freshness.
# Пороги настраиваются через env (см. константы выше).
# Возвращает "risk" / "warn" / "ok" — цвет точки в UI.
#
# Логика:
#   1) нет health_score → "ok" (нейтрально, не пугаем)
#   2) health_score < HEALTH_RISK_MAX → "risk"
#   3) needs_checkup + >STALE_MEETING_DAYS дней без встречи → "risk"
#      (даже если score приемлемый — просроченный чекап важнее)
#   4) health_score < HEALTH_WARN_MAX → "warn"
#   5) иначе → "ok"
# ──────────────────────────────────────────────────────────────
def health_to_status(
    health_score: Optional[float],
    needs_checkup: bool = False,
    last_meeting_date: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> str:
    if health_score is None and not needs_checkup:
        return "ok"

    if health_score is not None and health_score < HEALTH_RISK_MAX:
        return "risk"

    if needs_checkup:
        _now = now or datetime.utcnow()
        if not last_meeting_date:
            return "risk"  # нет ни одной встречи + требуется чекап = критично
        days = (_now.date() - last_meeting_date.date()).days
        if days > STALE_MEETING_DAYS:
            return "risk"

    if health_score is not None and health_score < HEALTH_WARN_MAX:
        return "warn"

    return "ok"


# ──────────────────────────────────────────────────────────────
# Next touchpoint — ближайшее из встречи или плановой даты чекапа.
# Менеджер видит "когда следующий контакт с клиентом" без разбора
# типа (встреча/чекап) — всё, что сегодня важно.
#
# ВАЖНО: принимает префетченные встречи как dict {client_id: datetime},
# чтобы избежать N+1. См. prefetch_next_meetings() ниже.
# ──────────────────────────────────────────────────────────────
def compute_next_touchpoint(
    client: Any,
    now: datetime,
    next_meetings_by_client: Dict[int, datetime],
) -> str:
    next_meeting_dt = next_meetings_by_client.get(client.id)

    next_checkup_dt: Optional[datetime] = None
    if client.last_checkup and client.segment in CHECKUP_INTERVALS:
        next_checkup_dt = client.last_checkup + timedelta(
            days=CHECKUP_INTERVALS[client.segment]
        )

    candidates = [d for d in (next_meeting_dt, next_checkup_dt) if d is not None]
    if not candidates:
        return "—"
    return relative_day(min(candidates), now)


def prefetch_next_meetings(db: Any, now: datetime, visible_ids: Optional[List[int]] = None) -> Dict[int, datetime]:
    """
    Один запрос вместо N: берём min(Meeting.date) по client_id, где date >= now.
    Возвращает {client_id: earliest_future_meeting_date}.
    """
    from sqlalchemy import func
    q = db.query(Meeting.client_id, func.min(Meeting.date)).filter(
        Meeting.date >= now,
        Meeting.client_id.isnot(None),
    ).group_by(Meeting.client_id)
    if visible_ids is not None:
        if not visible_ids:
            return {}
        q = q.filter(Meeting.client_id.in_(visible_ids))
    return {cid: dt for cid, dt in q.all()}


# ──────────────────────────────────────────────────────────────
# Форматеры — готовые, редактировать не нужно
# ──────────────────────────────────────────────────────────────
def format_gmv(mrr: Optional[float]) -> str:
    """4_800_000 → '₽ 4.8м', 890_000 → '₽ 890к', 0/None → '—'"""
    if not mrr or mrr <= 0:
        return "—"
    if mrr >= 1_000_000:
        val = f"{mrr / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"₽ {val}м"
    if mrr >= 1_000:
        return f"₽ {int(mrr / 1_000)}к"
    return f"₽ {int(mrr)}"


def format_delta(trend: Optional[str]) -> str:
    """revenue_trend: строка 'up'/'down'/'flat' или '+12%' → нормализуем."""
    if not trend:
        return "—"
    t = trend.strip()
    if t.startswith(("+", "−", "-")):
        return t
    low = t.lower()
    if low.startswith("up"):
        return "+—"
    if low.startswith("down"):
        return "−—"
    return "—"


def parse_trend(trend_value: Any) -> List[int]:
    """
    Пытаемся распарсить revenue_trend как JSON-массив [22,24,...].
    Если строка — пробуем json.loads. Иначе — пустой массив (UI покажет flat).
    """
    if not trend_value:
        return []
    if isinstance(trend_value, list):
        return [int(x) for x in trend_value if isinstance(x, (int, float))]
    if isinstance(trend_value, str):
        try:
            parsed = json.loads(trend_value)
            if isinstance(parsed, list):
                return [int(x) for x in parsed if isinstance(x, (int, float))]
        except (ValueError, TypeError):
            pass
    return []


def relative_day(dt: Optional[datetime], now: datetime) -> str:
    if not dt:
        return "—"
    delta = (dt.date() - now.date()).days
    if delta == 0:
        return f"сегодня, {dt.strftime('%H:%M')}"
    if delta == 1:
        return "завтра"
    if delta == -1:
        return "вчера"
    if 0 < delta <= 14:
        return f"через {delta} дн."
    if -14 <= delta < 0:
        return f"{-delta} дн. назад"
    return dt.strftime("%d.%m")


def pm_name(email: Optional[str]) -> str:
    """manager_email 'anna@any.ru' → 'anna'. Чтобы не тащить лишнее."""
    if not email:
        return "—"
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").title()


def stage_label(client: Any, now: datetime) -> str:
    """Грубая классификация стадии для дизайна."""
    if not client.last_meeting_date:
        return "новый"
    days = (now.date() - client.last_meeting_date.date()).days
    if client.needs_checkup or days > STALE_MEETING_DAYS:
        return "checkup"
    if (client.health_score or 1.0) < HEALTH_RISK_MAX:
        return "churn-риск"
    return "активный"


# ──────────────────────────────────────────────────────────────
# Entity → dict
# ──────────────────────────────────────────────────────────────
def client_to_design(
    client: Any,
    now: datetime,
    next_meetings_by_client: Dict[int, datetime],
) -> Dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name or "—",
        "seg": segment_label(client.segment),
        "pm": pm_name(client.manager_email),
        "next": compute_next_touchpoint(client, now, next_meetings_by_client),
        "status": health_to_status(
            client.health_score,
            needs_checkup=bool(client.needs_checkup),
            last_meeting_date=client.last_meeting_date,
            now=now,
        ),
        "gmv": format_gmv(client.mrr),
        "delta": format_delta(client.revenue_trend),
        "trend": parse_trend(client.revenue_trend),
        "days_since": (
            (now.date() - client.last_meeting_date.date()).days
            if client.last_meeting_date else None
        ),
        "stage": stage_label(client, now),
    }


# ──────────────────────────────────────────────────────────────
# Sidebar stats — живые цифры вместо хардкода в shell.jsx.
# Возвращает dict с ключами, которые читает обновлённый shell.
# ──────────────────────────────────────────────────────────────
def compute_sidebar_stats(
    db: Any,
    user: Any,
    visible_ids: Optional[List[int]],
    now: datetime,
) -> Dict[str, int]:
    from models import Client, Task, Meeting, Notification

    def _scope(q, column):
        if visible_ids is None:
            return q
        if not visible_ids:
            return q.filter(column == -1)
        return q.filter(column.in_(visible_ids))

    tasks_active = _scope(
        db.query(Task).filter(Task.status.in_(["plan", "in_progress"])),
        Task.client_id,
    ).count()

    overdue = _scope(
        db.query(Task).filter(
            Task.status.in_(["plan", "in_progress"]),
            Task.due_date.isnot(None),
            Task.due_date < now,
        ),
        Task.client_id,
    ).count()

    due_checkup = _scope(
        db.query(Client).filter(Client.needs_checkup.is_(True)),
        Client.id,
    ).count()

    clients_total = _scope(db.query(Client), Client.id).count()

    meetings_upcoming = _scope(
        db.query(Meeting).filter(Meeting.date >= now),
        Meeting.client_id,
    ).count()

    inbox = 0
    if user:
        inbox = db.query(Notification).filter(
            Notification.user_id == user.id,
            Notification.is_read.is_(False),
        ).count()

    return {
        "overdue":           overdue,
        "dueCheckup":        due_checkup,
        "tasksActive":       tasks_active,
        "clientsTotal":      clients_total,
        "meetingsUpcoming":  meetings_upcoming,
        "inbox":             inbox,
    }


_PRIORITY_MAP = {
    "critical": "critical",
    "high":     "high",
    "medium":   "med",
    "med":      "med",
    "low":      "low",
}

_TYPE_MAP = {
    "limit":       "limit",
    "email":       "email",
    "qbr":         "qbr",
    "call":        "call",
    "doc":         "doc",
    "investigate": "investigate",
}


def task_to_design(task: Any, now: datetime) -> Dict[str, Any]:
    # Срок → человекочитаемый лейбл
    due = "—"
    if task.due_date:
        days = (task.due_date.date() - now.date()).days
        if days < 0:
            due = f"просрочено {-days}д"
        elif days == 0:
            due = "сегодня"
        elif days == 1:
            due = "завтра"
        elif days <= 14:
            due = f"через {days} дн."
        else:
            due = task.due_date.strftime("%d %b")

    return {
        "id": task.id,
        "title": task.title,
        "client": task.client.name if task.client else "—",
        "due": due,
        "priority": _PRIORITY_MAP.get((task.priority or "").lower(), "med"),
        "type": _TYPE_MAP.get((task.task_type or "").lower(), task.task_type or "task"),
    }


def meeting_to_design(meeting: Any, now: datetime) -> Dict[str, Any]:
    when = meeting.date.strftime("%H:%M") if meeting.date else "—"
    day_label = relative_day(meeting.date, now) if meeting.date else "—"
    # Убираем "сегодня, 14:00" из day, оставляем только "сегодня"
    if "," in day_label:
        day_label = day_label.split(",")[0].strip()

    client_seg = segment_label(meeting.client.segment) if meeting.client else "—"
    mood = meeting.mood or ("risk" if meeting.sentiment_score and meeting.sentiment_score < 0.3 else "ok")

    return {
        "when": when,
        "day": day_label,
        "client": meeting.client.name if meeting.client else "—",
        "type": meeting.type or "sync",
        "seg": client_seg,
        "mood": mood,
    }


# ──────────────────────────────────────────────────────────────
# ACTIVITY — AuditLog → feed для правой колонки командного центра
# ──────────────────────────────────────────────────────────────
_ACTION_VERBS = {
    "create":         "создал(а)",
    "update":         "обновил(а)",
    "delete":         "удалил(а)",
    "deactivate":     "деактивировал(а)",
    "activate":       "активировал(а)",
    "reset_password": "сбросил(а) пароль",
    "change_role":    "сменил(а) роль",
    "login":          "зашёл(ла)",
    "logout":         "вышел(ла)",
    "sync":           "синхр.",
    "complete":       "завершил(а)",
    "cancel":         "отменил(а)",
    "send":           "отправил(а)",
}

_ACTION_MOOD = {
    "create":         "ok",
    "complete":       "ok",
    "sync":           "info",
    "update":         "info",
    "login":          "info",
    "logout":         "info",
    "delete":         "warn",
    "deactivate":     "warn",
    "cancel":         "warn",
    "reset_password": "info",
    "change_role":    "info",
    "send":           "ok",
}

_RESOURCE_RU = {
    "client":   "клиента",
    "task":     "задачу",
    "meeting":  "встречу",
    "user":     "пользователя",
    "checkup":  "чекап",
    "qbr":      "QBR",
    "followup": "фолоуап",
}


def relative_time_short(dt: Optional[datetime], now: datetime) -> str:
    """Короткий 'когда': '12 мин', '2 ч', '3 д'."""
    if not dt:
        return "—"
    delta = now - dt
    sec = int(delta.total_seconds())
    if sec < 60:
        return "сейчас"
    if sec < 3600:
        return f"{sec // 60} мин"
    if sec < 86400:
        return f"{sec // 3600} ч"
    days = sec // 86400
    if days < 14:
        return f"{days} д"
    return dt.strftime("%d.%m")


def activity_to_design(entry: Any, user_lookup: Dict[int, str], obj_lookup: Dict, now: datetime) -> Dict[str, Any]:
    """
    entry: AuditLog-запись (или SyncLog — тогда обёртка ниже нормализует)
    user_lookup: {user_id: "Имя"} — заранее достали одним запросом
    obj_lookup: {(resource_type, resource_id): "имя объекта"}
    """
    who = user_lookup.get(entry.user_id, "Система") if entry.user_id else "Система"
    verb = _ACTION_VERBS.get(entry.action or "", entry.action or "что-то сделал")
    resource_ru = _RESOURCE_RU.get(entry.resource_type or "", entry.resource_type or "")
    what = f"{verb} {resource_ru}".strip()
    obj = obj_lookup.get((entry.resource_type, entry.resource_id), "")
    if not obj and entry.new_values and isinstance(entry.new_values, dict):
        obj = str(entry.new_values.get("name") or entry.new_values.get("title") or entry.new_values.get("email") or "")
    return {
        "who":  who,
        "what": what,
        "obj":  obj or "—",
        "when": relative_time_short(entry.created_at, now),
        "mood": _ACTION_MOOD.get(entry.action or "", "info"),
    }


# ──────────────────────────────────────────────────────────────
# TOOLS — известный список интеграций + последний успешный sync
# ──────────────────────────────────────────────────────────────
KNOWN_TOOLS = [
    # (display name, detail, integration key в SyncLog)
    ("Merchrules",    "merchrules.any-platform.ru", "merchrules"),
    ("Airtable",      "base: am-hub-ops",           "airtable"),
    ("KTalk",         "tbank.ktalk.ru",             "ktalk"),
    ("Outlook",       "календарь + почта",          "outlook"),
    ("Telegram Bot",  "@amhub_bot",                 "telegram"),
    ("Diginetica",    "расширение браузера",        "diginetica"),
]


def tools_from_sync_logs(db: Any, now: datetime) -> List[Dict[str, Any]]:
    """Статус каждой известной интеграции по последнему SyncLog.
    ok = есть запись за последние 24ч со status='success' (иначе false)."""
    from models import SyncLog
    out = []
    for name, detail, key in KNOWN_TOOLS:
        last = (
            db.query(SyncLog)
              .filter(SyncLog.integration == key)
              .order_by(SyncLog.started_at.desc())
              .first()
        )
        ok = False
        sync_label = "—"
        if last:
            sync_label = relative_time_short(last.started_at, now)
            ok = (last.status == "success") and (now - last.started_at).total_seconds() < 86400
        out.append({"name": name, "detail": detail, "ok": ok, "sync": sync_label})
    return out


# ──────────────────────────────────────────────────────────────
# JOBS — последние N запусков синхронизаций (не путать с TOOLS:
# TOOLS = состояние интеграций, JOBS = лог запусков cron-задач)
# ──────────────────────────────────────────────────────────────
_INTEGRATION_SCHEDULE = {
    "merchrules": "каждые 15 мин",
    "airtable":   "03:00 UTC",
    "qwen":       "по запросу",
    "followup":   "ежедневно",
    "checkup":    "ежедневно",
    "digest":     "пт 18:00",
}


def jobs_from_sync_logs(db: Any, now: datetime, limit: int = 8) -> List[Dict[str, Any]]:
    """Последние запуски фоновых задач (SyncLog) — для карточки 'Автозадачи'."""
    from models import SyncLog
    rows = (
        db.query(SyncLog)
          .order_by(SyncLog.started_at.desc())
          .limit(limit)
          .all()
    )
    out = []
    for r in rows:
        key = (r.integration or "").lower()
        name_ru = {
            "merchrules": "Синхронизация Merchrules",
            "airtable":   "Airtable импорт клиентов",
            "qwen":       "Qwen: задачи из встреч",
            "followup":   "Утренний план команды",
            "checkup":    "Предикт churn-риска",
            "digest":     "Дайджест выходного дня",
        }.get(key, r.integration or "job")
        out.append({
            "name":     name_ru,
            "schedule": _INTEGRATION_SCHEDULE.get(key, "—"),
            "last":     relative_time_short(r.started_at, now),
            "ok":       (r.status == "success"),
        })
    return out
