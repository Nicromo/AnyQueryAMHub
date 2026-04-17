"""
Кастомные валидаторы для данных
"""

import re
import logging
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator, EmailStr

logger = logging.getLogger(__name__)


def validate_email(email: str) -> bool:
    """Валидировать email адрес"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_phone(phone: str) -> bool:
    """Валидировать номер телефона (международный формат)"""
    # Remove common separators
    phone_clean = re.sub(r'[\s\-\(\)\.]+', '', phone)
    # Should start with + and contain only digits
    return re.match(r'^\+?\d{10,15}$', phone_clean) is not None


def validate_url(url: str) -> bool:
    """Валидировать URL"""
    pattern = r'^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$'
    return re.match(pattern, url) is not None


def validate_airtable_id(record_id: str) -> bool:
    """Валидировать Airtable record ID"""
    # Airtable IDs are alphanumeric, usually 17 chars
    return bool(re.match(r'^[a-zA-Z0-9]{17}$', record_id))


def validate_future_date(date: datetime) -> bool:
    """Валидировать что дата в будущем"""
    return date > datetime.now()


def validate_date_range(start_date: datetime, end_date: datetime) -> bool:
    """Валидировать что end_date > start_date"""
    return end_date > start_date


def validate_string_length(value: str, min_length: int = 1, max_length: int = 255) -> bool:
    """Валидировать длину строки"""
    return min_length <= len(value) <= max_length


def validate_number_range(value: float, min_val: float = 0, max_val: float = 100) -> bool:
    """Валидировать диапазон числа"""
    return min_val <= value <= max_val


def validate_health_score(score: float) -> bool:
    """Валидировать health score (0-100)"""
    return validate_number_range(score, 0, 100)


def validate_priority(priority: str) -> bool:
    """Валидировать приоритет"""
    valid_priorities = ["low", "medium", "high", "critical"]
    return priority.lower() in valid_priorities


def validate_status(status: str, valid_statuses: list) -> bool:
    """Валидировать статус"""
    return status in valid_statuses


def validate_segment(segment: str) -> bool:
    """Валидировать сегмент клиента"""
    valid_segments = ["enterprise", "mid-market", "smb", "startup", "partner"]
    return segment.lower() in valid_segments


def validate_meeting_type(meeting_type: str) -> bool:
    """Валидировать тип встречи"""
    valid_types = ["qbr", "checkup", "planning", "feedback", "emergency"]
    return meeting_type.lower() in valid_types


def validate_checkup_type(checkup_type: str) -> bool:
    """Валидировать тип чекапа"""
    valid_types = ["quarterly", "annual", "urgent"]
    return checkup_type.lower() in valid_types


def validate_duration(minutes: int) -> bool:
    """Валидировать длительность встречи"""
    return 5 <= minutes <= 480  # 5 minutes to 8 hours


def validate_tags(tags: list[str]) -> bool:
    """Валидировать теги"""
    if not tags or not isinstance(tags, list):
        return False
    
    for tag in tags:
        if not isinstance(tag, str) or len(tag) < 2 or len(tag) > 50:
            return False
    
    return len(tags) <= 20  # Max 20 tags


def validate_name(name: str) -> bool:
    """Валидировать имя"""
    # Not empty, not too long, alphanumeric + spaces
    if not name or len(name) < 2 or len(name) > 255:
        return False
    
    return bool(re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9\s\-',\.]+$", name))


def sanitize_string(value: str) -> str:
    """Очистить строку от опасных символов"""
    # Remove leading/trailing spaces
    value = value.strip()
    
    # Replace multiple spaces with single space
    value = re.sub(r'\s+', ' ', value)
    
    return value


def truncate_string(value: str, max_length: int = 255) -> str:
    """Обрезать строку до максимальной длины"""
    if len(value) > max_length:
        return value[:max_length - 3] + "..."
    return value


class ClientValidator(BaseModel):
    """Валидатор для данных клиента"""
    name: str
    email: EmailStr
    phone: Optional[str] = None
    segment: Optional[str] = None

    @field_validator('email')
    def email_must_be_valid(cls, v):
        if not validate_email(v):
            raise ValueError('Invalid email format')
        return v

    @field_validator('phone')
    def phone_must_be_valid(cls, v):
        if v and not validate_phone(v):
            raise ValueError('Invalid phone format')
        return v

    @field_validator('segment')
    def segment_must_be_valid(cls, v):
        if v and not validate_segment(v):
            raise ValueError('Invalid segment')
        return v

    @field_validator('name')
    def name_must_be_valid(cls, v):
        if not validate_name(v):
            raise ValueError('Invalid name format')
        return sanitize_string(v)


class TaskValidator(BaseModel):
    """Валидатор для данных задачи"""
    title: str
    priority: str
    status: str
    due_date: Optional[datetime] = None

    @field_validator('title')
    def title_must_be_valid(cls, v):
        if not validate_string_length(v, min_length=5, max_length=500):
            raise ValueError('Title must be 5-500 characters')
        return sanitize_string(v)

    @field_validator('priority')
    def priority_must_be_valid(cls, v):
        if not validate_priority(v):
            raise ValueError('Invalid priority')
        return v

    @field_validator('status')
    def status_must_be_valid(cls, v):
        valid_statuses = ["open", "in_progress", "done", "cancelled"]
        if not validate_status(v, valid_statuses):
            raise ValueError('Invalid status')
        return v

    @field_validator('due_date')
    def due_date_must_be_valid(cls, v):
        if v and not validate_future_date(v):
            raise ValueError('Due date must be in the future')
        return v


class MeetingValidator(BaseModel):
    """Валидатор для данных встречи"""
    meeting_type: str
    duration_minutes: int
    meeting_date: datetime

    @field_validator('meeting_type')
    def meeting_type_must_be_valid(cls, v):
        if not validate_meeting_type(v):
            raise ValueError('Invalid meeting type')
        return v

    @field_validator('duration_minutes')
    def duration_minutes_must_be_valid(cls, v):
        if not validate_duration(v):
            raise ValueError('Duration must be between 5 and 480 minutes')
        return v

    @field_validator('meeting_date')
    def meeting_date_must_be_valid(cls, v):
        if not validate_future_date(v):
            raise ValueError('Meeting date must be in the future')
        return v
