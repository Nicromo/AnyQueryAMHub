"""
AM Hub — Полные модели данных
Workflow: встречи → фолоуап → задачи → roadmaps → QBR → план клиента
"""
import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, nullable=True)
    manager_email = Column(String, nullable=True)
    airtable_record_id = Column(String, nullable=True, unique=True)
    merchrules_account_id = Column(String, nullable=True, unique=True)
    site_ids = Column(JSONB, default=list)
    health_score = Column(Float, default=0.0)
    revenue_trend = Column(String, nullable=True)
    activity_level = Column(String, nullable=True)
    last_meeting_date = Column(DateTime, nullable=True)
    last_checkup = Column(DateTime, nullable=True)
    needs_checkup = Column(Boolean, default=False)
    open_tickets = Column(Integer, default=0)
    last_ticket_date = Column(DateTime, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    integration_metadata = Column(JSONB, default=dict)

    # QBR
    last_qbr_date = Column(DateTime, nullable=True)
    next_qbr_date = Column(DateTime, nullable=True)

    # План работы (JSONB)
    # { "goals": [], "actions": [], "quarterly_targets": {}, "notes": "" }
    account_plan = Column(JSONB, default=dict)

    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="client", cascade="all, delete-orphan")
    checkups = relationship("CheckUp", back_populates="client", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    merchrules_task_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, default="plan")  # plan/in_progress/review/done/blocked
    priority = Column(String, default="medium")
    created_at = Column(DateTime, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=True)
    source = Column(String, default="manual")  # manual/roadmap/checkup/followup/qbr
    created_from_meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)
    team = Column(String, nullable=True)
    task_type = Column(String, nullable=True)

    # Workflow подтверждения
    confirmed_at = Column(DateTime, nullable=True)  # Когда подтвердил выполнение
    confirmed_by = Column(String, nullable=True)  # Email подтвердившего

    # Roadmap push
    pushed_to_roadmap = Column(Boolean, default=False)
    roadmap_pushed_at = Column(DateTime, nullable=True)

    client = relationship("Client", back_populates="tasks")
    meeting = relationship("Meeting", back_populates="created_tasks")


class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    date = Column(DateTime)
    type = Column(String)  # checkup/qbr/kickoff/sync/other
    source = Column(String, default="internal")
    title = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    recording_url = Column(String, nullable=True)
    transcript_url = Column(String, nullable=True)
    mood = Column(String, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    attendees = Column(JSONB, default=list)
    external_id = Column(String, nullable=True)

    # Followup workflow
    followup_status = Column(String, default="pending")  # pending/filled/sent/skipped
    followup_text = Column(Text, nullable=True)
    followup_sent_at = Column(DateTime, nullable=True)
    followup_skipped = Column(Boolean, default=False)

    # QBR флаг
    is_qbr = Column(Boolean, default=False)

    created_tasks = relationship("Task", back_populates="meeting")
    client = relationship("Client", back_populates="meetings")


class CheckUp(Base):
    __tablename__ = "checkups"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    type = Column(String)  # quarterly/annual/monthly
    status = Column(String)  # overdue/scheduled/completed/cancelled
    scheduled_date = Column(DateTime)
    completed_date = Column(DateTime, nullable=True)
    priority = Column(Integer, default=0)
    merchrules_id = Column(String, nullable=True)
    client = relationship("Client", back_populates="checkups")

    @property
    def is_overdue(self):
        return self.status == "overdue" and self.scheduled_date < datetime.utcnow()


class QBR(Base):
    """Quarterly Business Review"""
    __tablename__ = "qbrs"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    quarter = Column(String, nullable=False)  # "2026-Q1"
    year = Column(Integer, nullable=False)
    date = Column(DateTime, nullable=True)  # Дата проведения
    status = Column(String, default="draft")  # draft/scheduled/completed/cancelled

    # Метрики за квартал
    metrics = Column(JSONB, default=dict)
    # { "revenue": {}, "tasks_completed": 0, "meetings_count": 0, "health_trend": "" }

    # Итоги
    summary = Column(Text, nullable=True)
    achievements = Column(JSONB, default=list)  # ["Задача 1 выполнена", ...]
    issues = Column(JSONB, default=list)
    next_quarter_goals = Column(JSONB, default=list)

    # Связанная встреча
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)

    client = relationship("Client")
    meeting = relationship("Meeting")


class AccountPlan(Base):
    """План работы по клиенту"""
    __tablename__ = "account_plans"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), unique=True)

    # Цели на квартал
    quarterly_goals = Column(JSONB, default=list)
    # [{ "goal": "...", "target": "...", "deadline": "...", "status": "..." }]

    # Действия
    action_items = Column(JSONB, default=list)
    # [{ "action": "...", "assignee": "...", "due": "...", "done": false }]

    # Заметки и стратегия
    notes = Column(Text, nullable=True)
    strategy = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, nullable=True)

    client = relationship("Client")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    role = Column(String, default="manager")
    is_active = Column(Boolean, default=True)
    hashed_password = Column(String, nullable=True)
    telegram_id = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    settings = Column(JSONB, default=dict)
    assigned_clients = relationship("Client", secondary="user_client_assignment", backref="assigned_managers")


class UserClientAssignment(Base):
    __tablename__ = "user_client_assignment"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), index=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    domain = Column(String, nullable=True)
    airtable_base_id = Column(String, nullable=True)
    merchrules_login = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    account_data = Column(JSONB, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String)
    resource_type = Column(String)
    resource_id = Column(Integer)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String)
    message = Column(Text)
    type = Column(String)  # info/warning/alert/success
    is_read = Column(Boolean, default=False)
    related_resource_type = Column(String, nullable=True)
    related_resource_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    read_at = Column(DateTime, nullable=True)


class SyncLog(Base):
    __tablename__ = "sync_logs"
    id = Column(Integer, primary_key=True, index=True)
    integration = Column(String, index=True)
    resource_type = Column(String)
    action = Column(String)
    status = Column(String)
    message = Column(Text, nullable=True)
    records_processed = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    sync_data = Column(JSONB, default=dict)
