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

from models import (
    CHECKUP_INTERVALS, Meeting, Task,
    FollowupTemplate, AutoTaskRule, AuditLog, Client, User, VoiceNote,
    RoadmapItem,
)
from cache import ttl_cache, invalidate


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

    # Ожидаемый чекап. База — last_checkup → last_meeting_date → last_sync_at → now.
    next_checkup_dt: Optional[datetime] = None
    if client.segment in CHECKUP_INTERVALS:
        base = (
            getattr(client, "last_checkup", None)
            or getattr(client, "last_meeting_date", None)
            or getattr(client, "last_sync_at", None)
        )
        if base:
            next_checkup_dt = base + timedelta(days=CHECKUP_INTERVALS[client.segment])
        else:
            # Новый клиент без истории — следующий чекап через интервал от now
            next_checkup_dt = now + timedelta(days=CHECKUP_INTERVALS[client.segment])

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


def prefetch_revenue_trends(db: Any, visible_ids: Optional[List[int]] = None, months: int = 6) -> Dict[int, Dict[str, Any]]:
    """Собирает ряды MRR помесячно для sparkline и считает delta% к предыдущему периоду.

    Возвращает: {client_id: {"trend": [int, int, ...], "delta": "+12%" | "−5%" | "—"}}
    """
    try:
        from models import RevenueEntry
    except Exception:
        return {}
    try:
        q = db.query(RevenueEntry).filter(RevenueEntry.client_id.isnot(None))
        if visible_ids is not None:
            if not visible_ids:
                return {}
            q = q.filter(RevenueEntry.client_id.in_(visible_ids))
        # Берём последние N*макс.клиентов записей и группируем на стороне Python
        rows = q.order_by(RevenueEntry.client_id, RevenueEntry.period.desc()).all()
    except Exception:
        return {}
    by_client: Dict[int, List[Any]] = {}
    for r in rows:
        by_client.setdefault(r.client_id, []).append(r)
    result: Dict[int, Dict[str, Any]] = {}
    for cid, rs in by_client.items():
        # Отсортируем по периоду возрастание
        rs_sorted = sorted(rs, key=lambda x: x.period or "")
        values = [float(r.mrr or 0) for r in rs_sorted[-months:]]
        trend = [int(v) for v in values]
        delta_str = "—"
        if len(values) >= 2 and values[-2] > 0:
            pct = round((values[-1] - values[-2]) / values[-2] * 100, 1)
            sign = "+" if pct >= 0 else "−"
            delta_str = f"{sign}{abs(pct)}%"
        result[cid] = {"trend": trend, "delta": delta_str}
    return result


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
    revenue_trends_by_client: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    lm_date = getattr(client, "last_meeting_date", None)
    lc_date = getattr(client, "last_checkup", None)
    contract_end = getattr(client, "contract_end", None)
    created_at = getattr(client, "created_at", None) or getattr(client, "last_sync_at", None)
    days_since_added = (
        (now.date() - created_at.date()).days if created_at else None
    )
    # Динамика: сначала из RevenueEntry, если не прокинули — фоллбек на client.revenue_trend
    rev_info = (revenue_trends_by_client or {}).get(client.id) if revenue_trends_by_client else None
    if rev_info:
        delta = rev_info.get("delta", "—")
        trend = rev_info.get("trend", [])
    else:
        delta = format_delta(client.revenue_trend)
        trend = parse_trend(client.revenue_trend)
    return {
        "id": client.id,
        "name": client.name or "—",
        "seg": segment_label(client.segment),
        "segment": client.segment or "",  # для фильтров в page_clients.jsx (_norm(c.segment))
        "pm": pm_name(client.manager_email),
        "manager_email": client.manager_email or "",
        "domain": getattr(client, "domain", "") or "",
        "next": compute_next_touchpoint(client, now, next_meetings_by_client),
        "status": health_to_status(
            client.health_score,
            needs_checkup=bool(client.needs_checkup),
            last_meeting_date=lm_date,
            now=now,
        ),
        "health_score": client.health_score,
        "needs_checkup": bool(getattr(client, "needs_checkup", False)),
        "gmv": format_gmv(client.mrr),
        "gmv_raw": client.mrr,
        "mrr": client.mrr,
        "delta": delta,
        "trend": trend,
        "revenue_trend": client.revenue_trend,
        "days_since": (
            (now.date() - lm_date.date()).days if lm_date else None
        ),
        "last_meeting_date": lm_date.isoformat() if lm_date else None,
        "last_checkup": lc_date.isoformat() if lc_date else None,
        "contract_end": contract_end.isoformat() if contract_end else None,
        "open_tickets": getattr(client, "open_tickets", 0) or 0,
        "nps_last": getattr(client, "nps_last", None),
        "days_since_added": days_since_added,
        "is_new": (days_since_added is not None and days_since_added <= 14),
        "stage": stage_label(client, now),
        "payment_status": getattr(client, "payment_status", None) or "unknown",
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
    """Wrapper: enables TTL caching while keeping db/visible_ids as live params."""
    _visible_key = tuple(sorted(visible_ids)) if visible_ids is not None else None
    return _compute_sidebar_stats_cached(user, db, _visible_key, now)


@ttl_cache(ttl=30, key_fn=lambda args, kwargs: str(args[0].id) if args else "default")
def _compute_sidebar_stats_cached(
    user: Any,
    db: Any,
    visible_ids_tuple: Any,
    now: datetime,
) -> Dict[str, int]:
    """Cached implementation — cache key is user.id, TTL 30s."""
    # Convert back from tuple to list (or None)
    visible_ids = list(visible_ids_tuple) if visible_ids_tuple is not None else None
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
    """Статус каждой известной интеграции:
      • сначала проверяется env-конфиг (есть ли токены/ключи) —
        без него интеграция offline независимо от SyncLog
      • если конфиг есть — последний успех в SyncLog за 24ч → online
    """
    from models import SyncLog

    # Карта проверки env-конфига. Если ни одна из переменных не задана —
    # интеграция считается не подключённой независимо от логов.
    def _configured(key: str) -> bool:
        import os
        envs = {
            "merchrules": ["MERCHRULES_URL", "MERCHRULES_EMAIL", "MERCHRULES_PASSWORD"],
            "airtable":   ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"],
            "ktalk":      ["KTALK_TOKEN", "KTALK_API_KEY"],
            "outlook":    ["OUTLOOK_TENANT_ID", "OUTLOOK_CLIENT_ID"],
            "telegram":   ["TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"],
            "diginetica": ["DIGINETICA_API_KEY"],
        }.get(key, [])
        return any(os.getenv(e) for e in envs)

    out = []
    for name, detail, key in KNOWN_TOOLS:
        configured = _configured(key)
        last = (
            db.query(SyncLog)
              .filter(SyncLog.integration == key)
              .order_by(SyncLog.started_at.desc())
              .first()
        )
        sync_label = "—"
        ok = False
        if last:
            sync_label = relative_time_short(last.started_at, now)
            if configured and last.status == "success" and (now - last.started_at).total_seconds() < 86400:
                ok = True
        out.append({
            "name": name,
            "detail": detail,
            "ok": ok,
            "sync": sync_label if configured else "не настроено",
            "configured": configured,
        })
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


# ══════════════════════════════════════════════════════════════
# Новые мапперы для design-страниц (аналитика/шаблоны/автозадачи/внутренние)
# ══════════════════════════════════════════════════════════════
def templates_to_design(db: Any, user: Any) -> List[Dict[str, Any]]:
    """Шаблоны текущего менеджера + глобальные (если такие будут).
    Usage считается по TaskComment пока не поддерживается — вернём 0."""
    q = db.query(FollowupTemplate)
    if user and user.id:
        q = q.filter((FollowupTemplate.user_id == user.id) | (FollowupTemplate.user_id.is_(None)))
    rows = q.order_by(FollowupTemplate.created_at.desc()).limit(40).all()
    return [{
        "id":       t.id,
        "name":     t.name,
        "category": t.category or "general",
        "body":     (t.content or "")[:400],
        "usage":    0,  # счётчик использования пока не отслеживается
    } for t in rows]


def auto_rules_to_design(db: Any, user: Any) -> List[Dict[str, Any]]:
    """Правила автозадач — для менеджера видны свои + глобальные.
    `hits` — кол-во Task с таким же task_title за последние 30 дней
    (грубая оценка срабатываний до появления полноценного rule_log).
    """
    q = db.query(AutoTaskRule)
    if user and (user.role or "") != "admin":
        q = q.filter((AutoTaskRule.user_id == user.id) | (AutoTaskRule.user_id.is_(None)))
    rows = q.order_by(AutoTaskRule.created_at.desc()).all()
    if not rows:
        return []

    thirty_ago = datetime.utcnow() - timedelta(days=30)
    titles = list({r.task_title for r in rows if r.task_title})
    hits_map: Dict[str, int] = {}
    if titles:
        hits_rows = db.query(Task.title).filter(
            Task.title.in_(titles),
            Task.created_at >= thirty_ago,
        ).all()
        for (title,) in hits_rows:
            hits_map[title] = hits_map.get(title, 0) + 1

    _TRIG_LABEL = {
        "health_drop":     "Падение Health Score",
        "days_no_contact": "Нет контакта N дней",
        "meeting_done":    "После встречи",
        "followup_sent":   "После отправки follow-up",
        "checkup_due":     "Чекап просрочен",
        "segment_match":   "Попадание в сегмент",
        "manual":          "Ручной",
    }
    out = []
    for r in rows:
        cfg = r.trigger_config or {}
        trig_extra = ""
        if r.trigger == "health_drop" and cfg.get("threshold"):
            trig_extra = f" · <{cfg['threshold']}"
        elif r.trigger == "days_no_contact" and cfg.get("days"):
            trig_extra = f" · {cfg['days']} дн."
        out.append({
            "id":   r.id,
            "on":   bool(r.is_active),
            "trig": _TRIG_LABEL.get(r.trigger, r.trigger) + trig_extra,
            "then": r.task_title,
            "hits": hits_map.get(r.task_title, 0),
        })
    return out


def auto_stats(db: Any, user: Any, now: datetime) -> Dict[str, Any]:
    """Wrapper: caches auto task stats per user, TTL 300s."""
    return _auto_stats_cached(user, db, now)


@ttl_cache(ttl=300, key_fn=lambda args, kwargs: str(args[0].id) if args else "default")
def _auto_stats_cached(user: Any, db: Any, now: datetime) -> Dict[str, Any]:
    """Cached auto stats implementation. TTL 300s."""
    thirty_ago = now - timedelta(days=30)

    # Базовый фильтр по правам
    tq = db.query(Task).filter(Task.created_at >= thirty_ago)
    if user and (user.role or "") != "admin":
        tq = tq.join(Client, Task.client_id == Client.id, isouter=True) \
               .filter((Client.manager_email == user.email) | (Task.client_id.is_(None)))
    recent_tasks = tq.all()
    tasks_30d = len(recent_tasks)

    # Среднее время реакции: для done-задач ищем самый ранний AuditLog update
    done_tasks = [t for t in recent_tasks if t.status == "done" and t.created_at]
    avg_reaction_min = None
    if done_tasks:
        task_ids = [t.id for t in done_tasks]
        logs = db.query(AuditLog).filter(
            AuditLog.resource_type == "task",
            AuditLog.resource_id.in_(task_ids),
            AuditLog.action.in_(["update", "complete", "done"]),
        ).order_by(AuditLog.created_at.asc()).all()
        first_update = {}
        for lg in logs:
            if lg.resource_id not in first_update and lg.created_at:
                first_update[lg.resource_id] = lg.created_at
        deltas = []
        for t in done_tasks:
            ts = first_update.get(t.id)
            if ts and ts > t.created_at:
                deltas.append((ts - t.created_at).total_seconds() / 60)
        if deltas:
            avg_reaction_min = int(sum(deltas) / len(deltas))

    return {
        "tasks_30d":        tasks_30d,
        "tasks_30d_delta":  None,
        "avg_reaction_min": avg_reaction_min,
        "avg_reaction_delta": None,
    }


def internal_tasks_to_design(db: Any, user: Any) -> List[Dict[str, Any]]:
    """Внутренние задачи команды (client_id IS NULL или source='internal')."""
    try:
        q = db.query(Task).filter(
            (Task.client_id.is_(None)) | (Task.source == "internal")
        ).order_by(Task.due_date.asc().nullslast()).limit(40)
    except Exception:
        q = db.query(Task).filter(Task.client_id.is_(None)).order_by(Task.due_date.asc().nullslast()).limit(40)
    rows = q.all()
    return [{
        "id":       t.id,
        "title":    t.title,
        "owner":    (t.team or "—"),
        "due":      t.due_date.strftime("%d %b") if t.due_date else "—",
        "priority": (t.priority or "low"),
        "done":     (t.status == "done"),
    } for t in rows]


def kpi_weekly(db: Any, user: Any, now: datetime, weeks: int = 13) -> List[Dict[str, Any]]:
    """Wrapper: caches weekly KPI per user, TTL 600s."""
    return _kpi_weekly_cached(user, db, now, weeks)


@ttl_cache(ttl=600, key_fn=lambda args, kwargs: str(args[0].id) if args else "default")
def _kpi_weekly_cached(user: Any, db: Any, now: datetime, weeks: int = 13) -> List[Dict[str, Any]]:
    """Cached weekly KPI implementation. TTL 600s."""
    out = []
    current_week_start = now - timedelta(days=now.weekday())
    current_week_start = current_week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    q = db.query(Task).filter(Task.status == "done")
    if user and (user.role or "") != "admin":
        q = q.join(Client, Task.client_id == Client.id, isouter=True) \
             .filter((Client.manager_email == user.email) | (Task.client_id.is_(None)))
    tasks = q.all()

    for i in range(weeks):
        week_start = current_week_start - timedelta(weeks=weeks - 1 - i)
        week_end   = week_start + timedelta(days=7)
        count = sum(
            1 for t in tasks
            if t.created_at and week_start <= t.created_at < week_end
        )
        out.append({
            "label":  f"W{week_start.isocalendar()[1]}",
            "value":  count,
            "active": week_end <= now + timedelta(days=1),
        })
    return out


def heatmap_activity(db: Any, user: Any, now: datetime,
                     visible_ids: Optional[List[int]], weeks: int = 7) -> Dict[str, Any]:
    """Wrapper: caches heatmap result per user, TTL 300s."""
    _visible_key = tuple(sorted(visible_ids)) if visible_ids is not None else None
    return _heatmap_activity_cached(user, db, now, _visible_key, weeks)


@ttl_cache(ttl=300, key_fn=lambda args, kwargs: str(args[0].id) if args else "default")
def _heatmap_activity_cached(user: Any, db: Any, now: datetime,
                              visible_ids_tuple: Any, weeks: int = 7) -> Dict[str, Any]:
    """Cached heatmap implementation. TTL 300s."""
    visible_ids = list(visible_ids_tuple) if visible_ids_tuple is not None else None
    cq = db.query(Client)
    if visible_ids is not None:
        if not visible_ids:
            return {"rows": [], "weeks": [], "matrix": []}
        cq = cq.filter(Client.id.in_(visible_ids))
    # Сортировка по GMV убыв. (клиенты с большим оборотом сверху)
    clients = cq.order_by(Client.gmv.desc().nullslast(), Client.id.desc()).limit(14).all()
    if not clients:
        return {"rows": [], "weeks": [], "matrix": []}

    current_week_start = now - timedelta(days=now.weekday())
    current_week_start = current_week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_starts = [current_week_start - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
    week_labels = [f"W{ws.isocalendar()[1]}" for ws in week_starts]

    earliest = week_starts[0]
    aq = db.query(AuditLog).filter(
        AuditLog.resource_type == "client",
        AuditLog.resource_id.in_([c.id for c in clients]),
        AuditLog.created_at >= earliest,
    )
    audits = aq.all()

    counts: Dict[int, Dict[int, int]] = {c.id: {i: 0 for i in range(weeks)} for c in clients}
    for a in audits:
        if not a.created_at:
            continue
        for i, ws in enumerate(week_starts):
            we = ws + timedelta(days=7)
            if ws <= a.created_at < we:
                counts[a.resource_id][i] = counts[a.resource_id].get(i, 0) + 1
                break

    max_v = max((v for c in counts.values() for v in c.values()), default=1) or 1
    matrix = []
    for c in clients:
        row = []
        for i in range(weeks):
            n = counts[c.id].get(i, 0)
            row.append({
                "value": round(n / max_v, 3),
                "risk":  (c.health_score is not None and c.health_score < 50),
            })
        matrix.append(row)

    return {
        "rows":   [c.name for c in clients],
        "weeks":  week_labels,
        "matrix": matrix,
    }


def team_response(db: Any, now: datetime) -> List[Dict[str, Any]]:
    """Wrapper: caches team response stats, TTL 300s (global, not per-user)."""
    return _team_response_cached(db, now)


@ttl_cache(ttl=300, key_fn=lambda args, kwargs: "global")
def _team_response_cached(db: Any, now: datetime) -> List[Dict[str, Any]]:
    """Cached team response implementation. TTL 300s."""
    thirty_ago = now - timedelta(days=30)
    users = db.query(User).filter(User.is_active == True).all()

    out = []
    for u in users:
        if not u.email:
            continue
        tasks = db.query(Task).join(Client, Task.client_id == Client.id) \
            .filter(Client.manager_email == u.email,
                    Task.status == "done",
                    Task.created_at >= thirty_ago).all()
        if len(tasks) < 10:  # стат-значимая выборка ≥10 реакций
            continue

        task_ids = [t.id for t in tasks]
        logs = db.query(AuditLog).filter(
            AuditLog.user_id == u.id,
            AuditLog.resource_type == "task",
            AuditLog.resource_id.in_(task_ids),
            AuditLog.action.in_(["update", "complete", "done"]),
        ).order_by(AuditLog.created_at.asc()).all()
        first_update = {}
        for lg in logs:
            if lg.resource_id not in first_update and lg.created_at:
                first_update[lg.resource_id] = lg.created_at

        deltas = []
        tmap = {t.id: t for t in tasks}
        for tid, ts in first_update.items():
            t = tmap.get(tid)
            if t and t.created_at and ts > t.created_at:
                deltas.append((ts - t.created_at).total_seconds() / 3600)

        if len(deltas) < 10:
            continue
        avg_h = sum(deltas) / len(deltas)
        if avg_h < 1:
            avg_str = f"{int(avg_h*60)}м"
        elif avg_h < 10:
            avg_str = f"{int(avg_h)}ч {int((avg_h%1)*60)}м"
        else:
            avg_str = f"{int(avg_h)}ч"
        tone = "signal" if avg_h < 1 else ("ok" if avg_h < 4 else "warn" if avg_h < 24 else "critical")
        out.append({"name": u.name or u.email, "avg": avg_str, "tone": tone})
    return out


def recent_files(db: Any, user: Any, limit: int = 8) -> List[Dict[str, Any]]:
    """Последние FileUpload + VoiceNote пользователя (admin видит всех)."""
    from models import FileUpload as _FU
    q = db.query(_FU).order_by(_FU.created_at.desc())
    if user and (user.role or "") != "admin":
        q = q.filter(_FU.user_id == user.id)
    files = q.limit(limit).all()
    out = [{
        "id":   f.id,
        "name": f.filename,
        "type": f"{(f.mime_type or 'файл').split('/')[-1]} · {round((f.size_bytes or 0)/1024)} KB",
        "date": f.created_at.strftime("%d %b") if f.created_at else "—",
        "url":  f"/api/files/{f.id}",
        "category": f.category or "misc",
    } for f in files]
    return out


def gmv_spark(db: Any, user: Any, now: datetime, days: int = 30) -> List[float]:
    """Wrapper: caches GMV spark data per user, TTL 600s."""
    return _gmv_spark_cached(user, db, now, days)


@ttl_cache(ttl=600, key_fn=lambda args, kwargs: str(args[0].id) if args else "default")
def _gmv_spark_cached(user: Any, db: Any, now: datetime, days: int = 30) -> List[float]:
    """Cached GMV spark implementation. TTL 600s."""
    try:
        from models import RevenueEntry
    except Exception:
        return []
    since = now - timedelta(days=days)
    q = db.query(RevenueEntry).filter(RevenueEntry.period >= since.strftime("%Y-%m-%d"))
    if user and (user.role or "") != "admin":
        q = q.join(Client, RevenueEntry.client_id == Client.id).filter(Client.manager_email == user.email)
    rows = q.all()
    if not rows:
        return []
    # Группировка по periоду (день)
    buckets: Dict[str, float] = {}
    for r in rows:
        k = str(r.period)[:10]
        buckets[k] = buckets.get(k, 0.0) + float(r.amount or 0)
    out = []
    for i in range(days):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(buckets.get(d, 0.0))
    return out


def day_kpi(db: Any, user: Any, now: datetime) -> Dict[str, Any]:
    """KPI дня для PageToday: встречи/задачи — план vs факт."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    mq = db.query(Meeting)
    tq = db.query(Task)
    if user and (user.role or "") != "admin":
        mq = mq.join(Client, Meeting.client_id == Client.id, isouter=True) \
               .filter((Client.manager_email == user.email) | (Meeting.client_id.is_(None)))
        tq = tq.join(Client, Task.client_id == Client.id, isouter=True) \
               .filter((Client.manager_email == user.email) | (Task.client_id.is_(None)))

    m_total = mq.filter(Meeting.date >= start, Meeting.date < end).count()
    m_done  = mq.filter(Meeting.date >= start, Meeting.date < end,
                        Meeting.followup_status.in_(["filled", "sent"])).count()
    t_total = tq.filter(Task.due_date >= start, Task.due_date < end).count()
    t_done  = tq.filter(Task.due_date >= start, Task.due_date < end, Task.status == "done").count()
    return {
        "meetings_done":  m_done,
        "meetings_total": m_total,
        "meetings_pct":   int(m_done / m_total * 100) if m_total else 0,
        "tasks_done":     t_done,
        "tasks_total":    t_total,
        "tasks_pct":      int(t_done / t_total * 100) if t_total else 0,
    }


def reminders_for_user(db: Any, user: Any, now: datetime, limit: int = 10) -> List[Dict[str, Any]]:
    """Персональные напоминания (Reminder) + high-priority просроченные задачи."""
    out: List[Dict[str, Any]] = []
    try:
        from models import Reminder
        rq = db.query(Reminder).filter(Reminder.user_id == user.id,
                                       Reminder.done == False,
                                       Reminder.remind_at <= now + timedelta(days=1)) \
                               .order_by(Reminder.remind_at.asc()).limit(limit).all()
        for r in rq:
            out.append({
                "t": r.remind_at.strftime("%H:%M") if r.remind_at else "",
                "msg": r.text,
                "done": bool(r.done),
            })
    except Exception:
        pass

    # Добавляем top-3 просроченных задачи как виртуальные напоминания
    if user and user.email:
        overdue = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True) \
            .filter((Client.manager_email == user.email) | (Task.client_id.is_(None)),
                    Task.status != "done",
                    Task.due_date != None,
                    Task.due_date < now) \
            .order_by(Task.due_date.asc()).limit(3).all()
        for t in overdue:
            out.append({
                "t": "просроч.",
                "msg": t.title,
                "done": False,
            })
    return out[:limit]


def qbr_calendar(db: Any, user: Any, now: datetime) -> List[Dict[str, Any]]:
    """Return QBR calendar data grouped by manager_email.

    Returns a flat list of dicts, each representing one QBR appointment:
    {
        id, client_id, client_name, quarter, year,
        date (YYYY-MM-DD or null), status, manager_email
    }
    Frontend groups by manager_email and lays out month columns.
    """
    from models import QBR, Client

    q = db.query(QBR).join(Client, QBR.client_id == Client.id, isouter=True)

    # Scope filter
    if user and (user.role or "") != "admin" and user.email:
        # show QBRs for this manager only
        q = q.filter(
            (QBR.manager_email == user.email) |
            (Client.manager_email == user.email)
        )

    rows = q.order_by(QBR.date.asc().nullslast()).limit(500).all()
    out = []
    for qbr in rows:
        client_name = qbr.client.name if qbr.client else None
        manager_email = (
            getattr(qbr, "manager_email", None)
            or (qbr.client.manager_email if qbr.client else None)
        )
        out.append({
            "id":            qbr.id,
            "client_id":     qbr.client_id,
            "client_name":   client_name or "—",
            "quarter":       qbr.quarter,
            "year":          qbr.year,
            "date":          qbr.date.strftime("%Y-%m-%d") if qbr.date else None,
            "status":        qbr.status or "draft",
            "manager_email": manager_email or "—",
        })
    return out


_ROADMAP_DEFAULT_ORDER = ["q1", "q2", "q3", "q4", "backlog"]


def roadmap_data(db: Any) -> List[Dict[str, Any]]:
    """Роадмап: группировка RoadmapItem по column_key в порядке Q1→Q4→Бэклог."""
    rows = db.query(RoadmapItem).order_by(
        RoadmapItem.column_key.asc(), RoadmapItem.order_idx.asc(), RoadmapItem.id.asc()
    ).all()
    if not rows:
        return []
    groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.column_key or "backlog"
        if key not in groups:
            groups[key] = {
                "key":   key,
                "title": r.column_title or key.upper(),
                "tone":  r.tone or "neutral",
                "items": [],
            }
        groups[key]["items"].append({
            "id":    r.id,
            "title": r.title,
            "desc":  r.description or "",
        })
    ordered_keys = [k for k in _ROADMAP_DEFAULT_ORDER if k in groups] + \
                   [k for k in groups if k not in _ROADMAP_DEFAULT_ORDER]
    return [groups[k] for k in ordered_keys]
