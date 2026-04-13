from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class Client(Base):
    __tablename__ = "clients"
    
    # Основные поля
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, nullable=True)
    manager_email = Column(String, nullable=True)
    
    # IDs из внешних систем
    airtable_record_id = Column(String, nullable=True, unique=True)
    merchrules_account_id = Column(String, nullable=True, unique=True)
    
    # Сайты
    site_ids = Column(JSONB, default=list)
    
    # Аналитика (из Merchrules)
    health_score = Column(Float, default=0.0)
    revenue_trend = Column(String, nullable=True)
    activity_level = Column(String, nullable=True)  # high/medium/low
    
    # Встречи и чекапы
    last_meeting_date = Column(DateTime, nullable=True)
    last_checkup = Column(DateTime, nullable=True)
    needs_checkup = Column(Boolean, default=False)
    
    # Саппорт (из Tbank Time)
    open_tickets = Column(Integer, default=0)
    last_ticket_date = Column(DateTime, nullable=True)
    
    # Синхронизация
    last_sync_at = Column(DateTime, nullable=True)
    integration_metadata = Column(JSONB, default=dict)  # Для хранения доп инфо по интеграциям
    
    # Связи
    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="client", cascade="all, delete-orphan")
    checkups = relationship("CheckUp", back_populates="client", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    merchrules_task_id = Column(String, nullable=True)  # ID в Merchrules
    
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, default="plan")  # plan/in_progress/blocked/done
    priority = Column(String, default="medium")  # low/medium/high/critical
    
    created_at = Column(DateTime, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=True)
    
    # Источник задачи
    source = Column(String, default="manual")  # manual/roadmap/checkup/feed/auto
    
    # Связь с встречей если создана из встречи
    created_from_meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)
    
    client = relationship("Client", back_populates="tasks")
    meeting = relationship("Meeting", back_populates="created_tasks")


class Meeting(Base):
    __tablename__ = "meetings"
    
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    
    date = Column(DateTime)
    type = Column(String)  # checkup/qbr/kickoff/sync/other
    source = Column(String, default="internal")  # ktalk/merchrules/internal
    
    # Контент встречи
    title = Column(String, nullable=True)
    summary = Column(Text, nullable=True)  # AI-generated summary
    transcript = Column(Text, nullable=True)  # Полная транскрипция
    
    # Запись встречи
    recording_url = Column(String, nullable=True)
    transcript_url = Column(String, nullable=True)
    
    # Анализ
    mood = Column(String, nullable=True)  # positive/neutral/negative
    sentiment_score = Column(Float, nullable=True)
    
    # Участники
    attendees = Column(JSONB, default=list)
    
    # Синхронизация
    external_id = Column(String, nullable=True)  # ID в Ktalk/Merchrules
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
    
    # Приоритет
    priority = Column(Integer, default=0)  # для сортировки overdue
    
    # Синхронизация
    merchrules_id = Column(String, nullable=True)
    
    client = relationship("Client", back_populates="checkups")
    
    @property
    def is_overdue(self):
        return self.status == "overdue" and self.scheduled_date < datetime.utcnow()


class SyncLog(Base):
    """Логирование синхронизации для отладки"""
    __tablename__ = "sync_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    integration = Column(String, index=True)  # airtable/merchrules/ktalk/tbank_time
    resource_type = Column(String)  # clients/tasks/meetings/checkups
    action = Column(String)  # pull/push/sync
    
    status = Column(String)  # success/error/partial
    message = Column(Text, nullable=True)
    
    records_processed = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    metadata = Column(JSONB, default=dict)
