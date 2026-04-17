"""
Pydantic schemas для валидации данных
"""
from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime
from typing import Optional, List
from enum import Enum


# ============================================================================
# Enums
# ============================================================================


class SegmentEnum(str, Enum):
    ENT = "ENT"
    SME_PLUS = "SME+"
    SME = "SME"
    SMB = "SMB"


class StatusEnum(str, Enum):
    PLAN = "plan"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class PriorityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MeetingTypeEnum(str, Enum):
    CHECKUP = "checkup"
    QBR = "qbr"
    KICKOFF = "kickoff"
    SYNC = "sync"
    OTHER = "other"


class CheckupStatusEnum(str, Enum):
    OVERDUE = "overdue"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ============================================================================
# CLIENT Schemas
# ============================================================================


class ClientBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    segment: Optional[SegmentEnum] = None
    manager_email: Optional[EmailStr] = None
    domain: Optional[str] = None


class ClientCreate(ClientBase):
    airtable_record_id: Optional[str] = None
    merchrules_account_id: Optional[str] = None
    site_ids: Optional[List[str]] = []


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    segment: Optional[SegmentEnum] = None
    manager_email: Optional[EmailStr] = None
    domain: Optional[str] = None
    health_score: Optional[float] = Field(None, ge=0, le=100)
    revenue_trend: Optional[str] = None


class ClientResponse(ClientBase):
    id: int
    health_score: float
    revenue_trend: Optional[str]
    activity_level: Optional[str]
    open_tickets: int
    last_meeting_date: Optional[datetime]
    last_checkup: Optional[datetime]
    needs_checkup: bool
    airtable_record_id: Optional[str]
    merchrules_account_id: Optional[str]
    last_sync_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============================================================================
# TASK Schemas
# ============================================================================


class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    status: StatusEnum = StatusEnum.PLAN
    priority: PriorityEnum = PriorityEnum.MEDIUM
    due_date: Optional[datetime] = None


class TaskCreate(TaskBase):
    client_id: int
    source: Optional[str] = "manual"


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    status: Optional[StatusEnum] = None
    priority: Optional[PriorityEnum] = None
    due_date: Optional[datetime] = None


class TaskResponse(TaskBase):
    id: int
    client_id: int
    merchrules_task_id: Optional[str]
    source: str
    created_at: datetime
    created_from_meeting_id: Optional[int]

    class Config:
        from_attributes = True


# ============================================================================
# MEETING Schemas
# ============================================================================


class MeetingBase(BaseModel):
    title: Optional[str] = None
    type: MeetingTypeEnum = MeetingTypeEnum.SYNC
    date: datetime
    summary: Optional[str] = None


class MeetingCreate(MeetingBase):
    client_id: int
    source: Optional[str] = "internal"
    attendees: Optional[List[str]] = []
    transcript: Optional[str] = None
    recording_url: Optional[str] = None


class MeetingUpdate(BaseModel):
    title: Optional[str] = None
    type: Optional[MeetingTypeEnum] = None
    summary: Optional[str] = None
    mood: Optional[str] = None
    transcript: Optional[str] = None
    recording_url: Optional[str] = None


class MeetingResponse(MeetingBase):
    id: int
    client_id: int
    source: str
    transcript: Optional[str]
    recording_url: Optional[str]
    mood: Optional[str]
    attendees: List[str]
    external_id: Optional[str]

    class Config:
        from_attributes = True


# ============================================================================
# CHECKUP Schemas
# ============================================================================


class CheckupBase(BaseModel):
    type: str = Field(..., min_length=1, max_length=50)
    scheduled_date: datetime
    priority: int = 0


class CheckupCreate(CheckupBase):
    client_id: int
    status: CheckupStatusEnum = CheckupStatusEnum.SCHEDULED


class CheckupUpdate(BaseModel):
    status: Optional[CheckupStatusEnum] = None
    scheduled_date: Optional[datetime] = None
    priority: Optional[int] = None
    completed_date: Optional[datetime] = None


class CheckupResponse(CheckupBase):
    id: int
    client_id: int
    status: str
    completed_date: Optional[datetime]
    merchrules_id: Optional[str]
    is_overdue: Optional[bool] = False

    class Config:
        from_attributes = True


# ============================================================================
# Pagination
# ============================================================================


class PaginationParams(BaseModel):
    skip: int = Field(0, ge=0)
    limit: int = Field(20, ge=1, le=100)

    class Config:
        json_schema_extra = {
            "example": {
                "skip": 0,
                "limit": 20,
            }
        }


class PaginatedResponse(BaseModel):
    data: list
    total: int
    skip: int
    limit: int
    has_more: bool


# ============================================================================
# Search & Filter
# ============================================================================


class ClientFilter(BaseModel):
    manager_email: Optional[str] = None
    segment: Optional[SegmentEnum] = None
    health_score_min: Optional[float] = Field(None, ge=0, le=100)
    health_score_max: Optional[float] = Field(None, ge=0, le=100)
    needs_checkup: Optional[bool] = None
    search: Optional[str] = None  # Поиск по названию, домену


class TaskFilter(BaseModel):
    client_id: Optional[int] = None
    status: Optional[StatusEnum] = None
    priority: Optional[PriorityEnum] = None
    source: Optional[str] = None
    due_date_from: Optional[datetime] = None
    due_date_to: Optional[datetime] = None


class MeetingFilter(BaseModel):
    client_id: Optional[int] = None
    type: Optional[MeetingTypeEnum] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    source: Optional[str] = None


# ============================================================================
# Error Response
# ============================================================================


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


class ValidationError(BaseModel):
    field: str
    message: str


# ============================================================================
# User/Auth
# ============================================================================


class UserCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    role: str = "manager"  # manager, admin, viewer


class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool

    class Config:
        from_attributes = True


# ============================================================================
# Sync Response
# ============================================================================


class SyncResponse(BaseModel):
    status: str  # success, error, partial
    synced: int
    errors: int = 0
    message: Optional[str] = None


class StatsResponse(BaseModel):
    total_clients: int
    total_tasks: int
    total_meetings: int
    overdue_checkups: int
    clients_with_health_score: int
    avg_health_score: Optional[float] = None
