"""
Л1: Google Calendar — построитель URL для добавления событий.
Не требует OAuth — генерирует ссылку для открытия в браузере.
Используется при планировании встреч из AM Hub.
"""
import urllib.parse
from datetime import datetime, timedelta


def build_gcal_url(
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str = "",
    location: str = "",
) -> str:
    """
    Возвращает прямую ссылку для добавления события в Google Calendar.
    Открывается в браузере без OAuth — пользователь сам сохраняет событие.
    """
    fmt = "%Y%m%dT%H%M%S"
    params: dict = {
        "text": title,
        "dates": f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
    }
    if description:
        params["details"] = description
    if location:
        params["location"] = location

    base = "https://calendar.google.com/calendar/r/eventedit"
    return f"{base}?{urllib.parse.urlencode(params)}"


def build_meeting_gcal_url(
    client_name: str,
    meeting_type: str,
    start_dt: datetime,
    duration_minutes: int = 60,
    notes: str = "",
    am_name: str = "",
) -> str:
    """
    Удобная обёртка: создаёт Google Calendar URL для встречи с клиентом.

    Args:
        client_name: Название клиента
        meeting_type: checkup / qbr / urgent / onboarding
        start_dt: Дата и время начала встречи
        duration_minutes: Продолжительность (по умолчанию 60 мин)
        notes: Дополнительные заметки в описание события
        am_name: Имя AM (добавляется в описание)
    """
    type_labels = {
        "checkup":   "Чекап",
        "qbr":       "QBR",
        "urgent":    "Срочная встреча",
        "onboarding": "Онбординг",
    }
    type_label = type_labels.get(meeting_type, meeting_type.capitalize())
    title = f"{type_label} — {client_name}"
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    desc_parts = [f"Клиент: {client_name}", f"Тип встречи: {type_label}"]
    if am_name:
        desc_parts.append(f"AM: {am_name}")
    if notes:
        desc_parts.append(f"\nЗаметки:\n{notes}")

    return build_gcal_url(
        title=title,
        start_dt=start_dt,
        end_dt=end_dt,
        description="\n".join(desc_parts),
    )


def build_checkup_gcal_url(client: dict, start_dt: datetime, am_name: str = "") -> str:
    """
    Быстрый построитель URL для чекапа.
    client — словарь с полями name, segment.
    """
    segment = client.get("segment", "")
    duration = 30 if segment in ("SME", "SELF") else 60  # ENT/SME+ — час, остальные — 30 мин
    notes = f"Сегмент: {segment}"
    if client.get("am_name"):
        am_name = am_name or client["am_name"]
    return build_meeting_gcal_url(
        client_name=client["name"],
        meeting_type="checkup",
        start_dt=start_dt,
        duration_minutes=duration,
        notes=notes,
        am_name=am_name,
    )


def parse_datetime_from_form(date_str: str, time_str: str = "10:00") -> datetime:
    """
    Парсит дату из формы (YYYY-MM-DD) и время (HH:MM).
    Возвращает datetime объект.
    """
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
