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

# Интервалы чекапов по сегментам (дни)
CHECKUP_INTERVALS = {"SS": 180, "SMB": 90, "SME": 60, "ENT": 30, "SME+": 60, "SME-": 60}


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

    # Финансы (быстрый доступ)
    mrr = Column(Float, default=0.0)
    nps_last = Column(Integer, nullable=True)
    nps_date = Column(DateTime, nullable=True)

    # Платёжный статус (для клиентского хаба /client/{id})
    payment_status   = Column(String, default="active")    # active | overdue | suspended | trial | unknown
    payment_due_date = Column(DateTime, nullable=True)     # до какого числа оплачено/ожидается
    payment_amount   = Column(Float, nullable=True)        # сумма текущего периода

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

    @property
    def text(self):
        """Alias для совместимости с шаблонами."""
        return self.title or ""

    @text.setter
    def text(self, value):
        self.title = value


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


class CheckupResult(Base):
    """Результаты автоматического чекапа из расширения Search Quality Checkup"""
    __tablename__ = "checkup_results"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), index=True)
    cabinet_id = Column(String, nullable=True)          # ID кабинета Diginetica
    query_type = Column(String, default="top")          # top/random/zero/zeroquery
    manager_name = Column(String, nullable=True)
    mode = Column(String, nullable=True)                # api/site
    total_queries = Column(Integer, default=0)
    avg_score = Column(Float, nullable=True)
    score_dist = Column(JSONB, default=dict)            # {0: N, 1: N, 2: N, 3: N}
    results = Column(JSONB, default=list)               # полные результаты
    created_at = Column(DateTime, default=datetime.utcnow)
    client = relationship("Client", backref="checkup_results")


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

    # Презентация и выжимка
    presentation_url = Column(String, nullable=True)  # Ссылка на презентацию
    executive_summary = Column(Text, nullable=True)  # Краткая выжимка для руководства
    future_work = Column(JSONB, default=list)  # [{"task": "...", "quarter": "...", "priority": "..."}]
    key_insights = Column(JSONB, default=list)  # Ключевые инсайты

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


class ClientNote(Base):
    """Заметки к клиенту"""
    __tablename__ = "client_notes"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    content = Column(Text, nullable=False)
    is_pinned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship("Client")
    user = relationship("User")


class TaskComment(Base):
    """Комментарии к задачам"""
    __tablename__ = "task_comments"
    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("tasks.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task")
    user = relationship("User")


class FollowupTemplate(Base):
    """Шаблоны фолоуапов"""
    __tablename__ = "followup_templates"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String, default="general")  # general/qbr/kickoff/sync/checkup
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class VoiceNote(Base):
    """Голосовые заметки к встречам"""
    __tablename__ = "voice_notes"
    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"))
    client_id = Column(Integer, ForeignKey("clients.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    audio_url = Column(String, nullable=True)
    transcription = Column(Text, nullable=True)
    duration_seconds = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    meeting = relationship("Meeting")
    client = relationship("Client")
    user = relationship("User")

class AutoTaskRule(Base):
    """Правила автоматического создания задач по триггерам."""
    __tablename__ = "auto_task_rules"
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True)  # None = глобальное
    name        = Column(String, nullable=False)
    is_active   = Column(Boolean, default=True)
    trigger     = Column(String, nullable=False)
    # Триггеры: health_drop | days_no_contact | meeting_done |
    #           followup_sent | checkup_due | segment_match | manual
    trigger_config = Column(JSONB, default=dict)
    # health_drop:      {"threshold": 50, "drop_pct": 15}
    # days_no_contact:  {"days": 30, "segments": ["Enterprise"]}
    # meeting_done:     {"meeting_types": ["checkup","qbr"]}
    # followup_sent:    {"delay_days": 3}
    task_title      = Column(String, nullable=False)
    task_description= Column(Text, nullable=True)
    task_priority   = Column(String, default="medium")
    task_due_days   = Column(Integer, default=3)   # через N дней от триггера
    task_type       = Column(String, default="followup")
    segment_filter  = Column(JSONB, default=list)  # [] = все сегменты
    actions            = Column(JSONB, default=list)  # [{"type":"create_task","params":{...}}, ...]
    trigger_count      = Column(Integer, default=0)
    last_triggered_at  = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User", backref="auto_task_rules")

class ClientHistory(Base):
    """История изменений клиента — audit log."""
    __tablename__ = "client_history"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    field      = Column(String, nullable=False)       # что изменилось
    old_value  = Column(Text, nullable=True)
    new_value  = Column(Text, nullable=True)
    event_type = Column(String, default="update")     # update|create|delete|note|meeting|task|checkup
    comment    = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    client = relationship("Client", backref="history")
    user   = relationship("User")


class AIChat(Base):
    """AI-чат по клиенту — история диалогов."""
    __tablename__ = "ai_chats"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), nullable=True)
    user_id    = Column(Integer, ForeignKey("users.id"), index=True)
    role       = Column(String, nullable=False)   # user | assistant
    content    = Column(Text, nullable=False)
    model      = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    client = relationship("Client")
    user   = relationship("User")


class TelegramSubscription(Base):
    """Подписки на Telegram уведомления."""
    __tablename__ = "telegram_subscriptions"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), unique=True)
    chat_id    = Column(String, nullable=False)
    is_active  = Column(Boolean, default=True)
    notify_overdue    = Column(Boolean, default=True)
    notify_health_drop= Column(Boolean, default=True)
    notify_tasks      = Column(Boolean, default=True)
    notify_daily      = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", backref="tg_subscription")

class MeetingComment(Base):
    """Комментарии к встречам."""
    __tablename__ = "meeting_comments"
    id         = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    meeting    = relationship("Meeting", backref="comments")
    user       = relationship("User")


class ClientAttachment(Base):
    """Вложения к клиенту (файлы, документы)."""
    __tablename__ = "client_attachments"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    filename   = Column(String, nullable=False)
    file_key   = Column(String, nullable=False)   # S3/R2 key
    file_size  = Column(Integer, default=0)       # bytes
    mime_type  = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    client     = relationship("Client", backref="attachments")
    user       = relationship("User")


class ChurnScore(Base):
    """Скоринг риска оттока — пересчитывается еженедельно."""
    __tablename__ = "churn_scores"
    id          = Column(Integer, primary_key=True, index=True)
    client_id   = Column(Integer, ForeignKey("clients.id"), unique=True, index=True)
    score       = Column(Float, default=0.0)      # 0-100, выше = больше риск
    risk_level  = Column(String, default="low")   # low|medium|high|critical
    factors     = Column(JSONB, default=dict)      # {days_no_contact, health_trend, ...}
    explanation = Column(Text, nullable=True)      # AI объяснение
    calculated_at = Column(DateTime, default=datetime.utcnow)
    client      = relationship("Client", backref="churn_score", uselist=False)


class OnboardingProgress(Base):
    """Прогресс онбординга нового менеджера."""
    __tablename__ = "onboarding_progress"
    id        = Column(Integer, primary_key=True, index=True)
    user_id   = Column(Integer, ForeignKey("users.id"), unique=True)
    completed = Column(JSONB, default=list)  # список выполненных шагов
    skipped   = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    user      = relationship("User", backref="onboarding")


# ── DB Indexes для горячих запросов ──────────────────────────────────────────
from sqlalchemy import Index

# Самые частые: clients by manager_email
Index("ix_clients_manager_email", Client.manager_email)
# Tasks by status for kanban
Index("ix_tasks_client_status", Task.client_id, Task.status)
# History queries
Index("ix_client_history_client_date", ClientHistory.client_id, ClientHistory.created_at)
# Notifications
Index("ix_notifications_user_read", Notification.user_id)



# ── Блок 1: Финансы ──────────────────────────────────────────────────────────

class RevenueEntry(Base):
    """MRR/ARR история — одна запись = один месяц."""
    __tablename__ = "revenue_entries"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    period     = Column(String, nullable=False)   # "2026-03" (YYYY-MM)
    mrr        = Column(Float, default=0.0)       # месячная выручка
    arr        = Column(Float, nullable=True)     # годовая (mrr * 12 если не задана)
    currency   = Column(String, default="RUB")
    note       = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_by = Column(String, nullable=True)
    client     = relationship("Client", backref="revenue_history")


class UpsellEvent(Base):
    """Апсейл / Даунсейл событие."""
    __tablename__ = "upsell_events"
    id          = Column(Integer, primary_key=True, index=True)
    client_id   = Column(Integer, ForeignKey("clients.id"), index=True)
    event_type  = Column(String, nullable=False)  # upsell|downsell|expansion|churn_risk
    status      = Column(String, default="identified")  # identified|in_progress|won|lost|postponed
    amount_before = Column(Float, nullable=True)  # MRR до
    amount_after  = Column(Float, nullable=True)  # MRR после (ожидаемый)
    delta         = Column(Float, nullable=True)  # amount_after - amount_before
    description = Column(Text, nullable=True)
    owner_email = Column(String, nullable=True)
    due_date    = Column(DateTime, nullable=True)
    closed_at   = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    created_by  = Column(String, nullable=True)
    client      = relationship("Client", backref="upsell_events")


# ── Блок 2: Health Score история ─────────────────────────────────────────────

class HealthSnapshot(Base):
    """Снимок health score — пишется при каждом пересчёте."""
    __tablename__ = "health_snapshots"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    score      = Column(Float, nullable=False)       # 0.0–1.0
    components = Column(JSONB, default=dict)         # {meetings:0.8, tasks:0.5, tickets:0.9, nps:0.7}
    calculated_at = Column(DateTime, default=datetime.utcnow, index=True)
    client     = relationship("Client", backref="health_history")


class NPSEntry(Base):
    """NPS / CSAT оценка от клиента."""
    __tablename__ = "nps_entries"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    score      = Column(Integer, nullable=False)     # NPS: -100..100 или CSAT: 1..10
    type       = Column(String, default="nps")       # nps|csat
    comment    = Column(Text, nullable=True)
    source     = Column(String, default="manual")    # manual|survey|import
    recorded_at = Column(DateTime, default=datetime.utcnow)
    recorded_by = Column(String, nullable=True)
    client     = relationship("Client", backref="nps_history")


Index("ix_revenue_client_period", RevenueEntry.client_id, RevenueEntry.period)
Index("ix_health_snapshots_client_date", HealthSnapshot.client_id, HealthSnapshot.calculated_at)
Index("ix_nps_client_date", NPSEntry.client_id, NPSEntry.recorded_at)


# ── Блок 3: Клиентский хаб /client/{id} ──────────────────────────────────────

class ClientContact(Base):
    """Контакты клиента (ЛПР, технарь, финансист и т.д.)."""
    __tablename__ = "client_contacts"
    id         = Column(Integer, primary_key=True, index=True)
    client_id  = Column(Integer, ForeignKey("clients.id"), index=True)
    name       = Column(String, nullable=False)
    role       = Column(String, nullable=True)          # decision_maker | tech | finance | other
    position   = Column(String, nullable=True)          # должность свободным текстом
    email      = Column(String, nullable=True)
    phone      = Column(String, nullable=True)
    telegram   = Column(String, nullable=True)
    is_primary = Column(Boolean, default=False)
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    client     = relationship("Client", backref="contacts")


class ClientProduct(Base):
    """Подключённые продукты клиента."""
    __tablename__ = "client_products"
    id            = Column(Integer, primary_key=True, index=True)
    client_id     = Column(Integer, ForeignKey("clients.id"), index=True)
    code          = Column(String, nullable=False)      # search | recommendations | banners | personalization | email | push | seo
    name          = Column(String, nullable=False)      # "Поиск", "Рекомендации"
    status        = Column(String, default="active")    # active | paused | trial | disabled
    activated_at  = Column(DateTime, nullable=True)
    extra         = Column(JSONB, default=dict)         # настройки/версия
    client        = relationship("Client", backref="products")


class ClientMerchRule(Base):
    """Кэш правил мерчандайзинга из Merchrules."""
    __tablename__ = "client_merch_rules"
    id           = Column(Integer, primary_key=True, index=True)
    client_id    = Column(Integer, ForeignKey("clients.id"), index=True)
    merchrules_id = Column(String, nullable=True)       # id в Merchrules
    name         = Column(String, nullable=False)
    rule_type    = Column(String, nullable=True)        # boost | filter | pin | synonym | redirect
    status       = Column(String, default="active")     # active | paused | archived
    priority     = Column(Integer, default=0)
    config       = Column(JSONB, default=dict)
    last_synced  = Column(DateTime, nullable=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    client       = relationship("Client", backref="merch_rules")


class ClientFeed(Base):
    """Фиды клиента (каталог, наличие, цены и т.д.)."""
    __tablename__ = "client_feeds"
    id             = Column(Integer, primary_key=True, index=True)
    client_id      = Column(Integer, ForeignKey("clients.id"), index=True)
    feed_type      = Column(String, nullable=False)     # catalog | availability | price | reviews | custom
    name           = Column(String, nullable=True)
    url            = Column(String, nullable=True)
    status         = Column(String, default="ok")       # ok | warning | error | disabled
    last_updated   = Column(DateTime, nullable=True)
    sku_count      = Column(Integer, default=0)
    errors_count   = Column(Integer, default=0)
    last_error     = Column(Text, nullable=True)
    schedule       = Column(String, nullable=True)      # hourly | daily | manual
    client         = relationship("Client", backref="feeds")


# ── Блок 4: Support Tickets (Tbank Time / Mattermost) ────────────────────────

class SupportTicket(Base):
    """Тикет поддержки из Tbank Time (Mattermost)."""
    __tablename__ = "support_tickets"
    id                   = Column(Integer, primary_key=True, index=True)
    client_id            = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    source               = Column(String, default="tbank_time")     # tbank_time | manual
    external_id          = Column(String, unique=True, index=True)  # Mattermost post id (root)
    external_url         = Column(String, nullable=True)
    channel_id           = Column(String, nullable=True)
    title                = Column(String, nullable=True)
    body                 = Column(Text, nullable=True)
    status               = Column(String, default="open")           # open | in_progress | resolved | closed
    priority             = Column(String, default="normal")
    author               = Column(String, nullable=True)
    author_name          = Column(String, nullable=True)
    external_client_id   = Column(String, nullable=True, index=True)  # Распарсенный ID из текста
    comments_count       = Column(Integer, default=0)
    last_comment_at      = Column(DateTime, nullable=True)
    last_comment_snippet = Column(Text, nullable=True)
    opened_at            = Column(DateTime, nullable=True, index=True)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at          = Column(DateTime, nullable=True)
    raw                  = Column(JSONB, default=dict)

    client = relationship("Client", backref="support_tickets")


class TicketComment(Base):
    """Комментарий к тикету — сообщение из треда Mattermost."""
    __tablename__ = "ticket_comments"
    id           = Column(Integer, primary_key=True, index=True)
    ticket_id    = Column(Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    external_id  = Column(String, unique=True, index=True)
    author       = Column(String, nullable=True)
    author_name  = Column(String, nullable=True)
    body         = Column(Text, nullable=True)
    posted_at    = Column(DateTime, nullable=True)
    raw          = Column(JSONB, default=dict)
    ticket       = relationship("SupportTicket", backref="comments")
