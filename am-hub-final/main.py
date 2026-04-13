"""
AM Hub - FastAPI main application with full integration
Полная интеграция с:
- Pydantic schemas для валидации
- JWT authentication
- Role-based access control
- Email уведомления
- File upload/export
- WebSocket real-time
- Rate limiting и логирование
"""

import os
import io
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import (
    FastAPI, Request, Depends, HTTPException, UploadFile, File, 
    Query, WebSocket, WebSocketDisconnect, status
)
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

# Configuration
from database import engine, get_db, Base, init_db, SessionLocal

# Models
from models import Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog, Notification

# Schemas
from schemas import (
    ClientCreate, ClientUpdate, ClientResponse, ClientFilter,
    TaskCreate, TaskUpdate, TaskResponse, TaskFilter,
    MeetingCreate, MeetingUpdate, MeetingResponse, MeetingFilter,
    CheckupCreate, CheckupUpdate, CheckupResponse,
    UserCreate, UserResponse,
    PaginatedResponse, ErrorResponse, StatsResponse,
)

# Auth & Security
from auth import (
    get_current_user, get_current_admin, 
    authenticate_user, create_user, 
    check_client_access, ensure_client_access,
    log_audit, create_access_token
)

# Error handling
from error_handlers import (
    ValidationError, ResourceNotFoundError, UnauthorizedError,
    ForbiddenError, ConflictError, BadRequestError, handle_db_error, log_error
)

# Middleware
from middlewares import (
    LoggingMiddleware, RateLimitMiddleware,
    ErrorHandlingMiddleware, SecurityHeadersMiddleware
)

# Services
from email_service import (
    send_morning_plan, send_overdue_checkup_alert, send_task_created,
    EmailType, get_email_service
)
from file_service import (
    FileProcessor, BulkImporter, BulkExporter, FileFormat
)
from websocket_manager import (
    ConnectionManager, handle_websocket_connection, emit_task_created,
    emit_task_updated, emit_client_updated, emit_notification, manager
)
from validators import ClientValidator, TaskValidator, MeetingValidator

# Integrations
from integrations.airtable import get_clients as sync_clients_airtable
from integrations.merchrules_extended import (
    fetch_account_analytics, fetch_checkups, fetch_roadmap_tasks,
    fetch_meetings as fetch_meetings_merchrules
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

templates = Jinja2Templates(directory="templates")


# ============================================================================
# LIFESPAN
# ============================================================================

def _seed_demo_data(db):
    """Заполнить БД демо-данными если пусто"""
    if db.query(Client).count() > 0:
        return  # Уже есть данные

    import random
    from datetime import timedelta as td

    logger.info("🌱 Seeding demo data...")

    segments = ["ENT", "SME+", "SME-", "SMB", "SS"]
    companies = {
        "ENT": ["Сбербанк", "Яндекс", "МТС", "Ростелеком", "Тинькофф"],
        "SME+": ["Ozon", "Wildberries", "Lamoda", "DNS", "М.Видео"],
        "SME-": ["Ситилинк", "Эльдорадо", "Эксперт", "Поларис", "Беру"],
        "SMB": ["Магазин у дома", "Кофейня №1", "Студия красоты", "Фитнес-клуб", "Автосервис"],
        "SS": ["ИП Иванов", "ИП Петров", "ИП Сидоров", "ИП Козлов", "ИП Новиков"],
    }
    managers = ["ivan@company.ru", "maria@company.ru", "alex@company.ru"]
    task_titles = [
        "Настроить трекинг событий", "Проверить качество поиска",
        "Интегрировать API рекомендаций", "Обновить модель ранжирования",
        "Провести A/B тест", "Оптимизировать выдачу",
        "Добавить новые фильтры", "Настроить персонализацию",
    ]
    statuses = ["plan", "in_progress", "done", "blocked"]
    priorities = ["low", "medium", "high"]

    for segment, names in companies.items():
        for name in names:
            client = Client(
                name=name, segment=segment,
                manager_email=random.choice(managers),
                health_score=round(random.uniform(0.3, 1.0), 2),
                activity_level=random.choice(["high", "medium", "low"]),
                open_tickets=random.randint(0, 5),
                site_ids=[random.randint(100, 9999)],
                last_meeting_date=datetime.now() - td(days=random.randint(1, 60)),
                needs_checkup=random.choice([True, False]),
                revenue_trend=random.choice(["growing", "stable", "declining"]),
            )
            db.add(client)
            db.flush()

            # Задачи
            for _ in range(random.randint(2, 5)):
                db.add(Task(
                    client_id=client.id,
                    title=random.choice(task_titles),
                    description=f"Задача для {name}",
                    status=random.choice(statuses),
                    priority=random.choice(priorities),
                    created_at=datetime.now() - td(days=random.randint(1, 30)),
                    due_date=datetime.now() + td(days=random.randint(-5, 30)),
                ))

    db.commit()
    logger.info(f"✅ Seeded {len(companies)} segments with demo data")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup/shutdown"""
    try:
        init_db()
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            _run_migrations(db)
            _seed_demo_data(db)
        logger.info("✅ Database connected")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")

    yield


def _run_migrations(db):
    """Добавить отсутствующие колонки в существующие таблицы"""
    # Получить существующие колонки для каждой таблицы
    tables_columns = {}
    for table in ["clients", "tasks", "meetings", "checkups", "users", "accounts",
                   "audit_logs", "notifications", "sync_logs"]:
        cols = db.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = :table
            """),
            {"table": table}
        ).fetchall()
        tables_columns[table] = {row[0] for row in cols}

    # Список колонок для добавления
    pending = [
        # Clients
        ("clients", "domain", "VARCHAR"),
        ("clients", "segment", "VARCHAR"),
        ("clients", "account_id", "INTEGER REFERENCES accounts(id)"),
        ("clients", "manager_email", "VARCHAR"),
        ("clients", "merchrules_account_id", "VARCHAR"),
        ("clients", "site_ids", "JSONB"),
        ("clients", "health_score", "FLOAT"),
        ("clients", "revenue_trend", "VARCHAR"),
        ("clients", "activity_level", "VARCHAR"),
        ("clients", "open_tickets", "INTEGER DEFAULT 0"),
        ("clients", "last_ticket_date", "TIMESTAMP"),
        ("clients", "integration_metadata", "JSONB"),
        ("clients", "airtable_record_id", "VARCHAR"),
        ("clients", "last_checkup", "TIMESTAMP"),
        ("clients", "needs_checkup", "BOOLEAN DEFAULT FALSE"),
        ("clients", "last_meeting_date", "TIMESTAMP"),
        ("clients", "last_sync_at", "TIMESTAMP"),
        # Tasks
        ("tasks", "title", "VARCHAR NOT NULL"),
        ("tasks", "description", "TEXT"),
        ("tasks", "status", "VARCHAR"),
        ("tasks", "priority", "VARCHAR"),
        ("tasks", "created_at", "TIMESTAMP"),
        ("tasks", "client_id", "INTEGER REFERENCES clients(id)"),
        ("tasks", "merchrules_task_id", "VARCHAR"),
        ("tasks", "source", "VARCHAR DEFAULT 'manual'"),
        ("tasks", "created_from_meeting_id", "INTEGER REFERENCES meetings(id)"),
        ("tasks", "due_date", "TIMESTAMP"),
        ("tasks", "team", "VARCHAR"),
        ("tasks", "task_type", "VARCHAR"),
        # Meetings
        ("meetings", "client_id", "INTEGER REFERENCES clients(id)"),
        ("meetings", "source", "VARCHAR DEFAULT 'internal'"),
        ("meetings", "title", "VARCHAR"),
        ("meetings", "summary", "TEXT"),
        ("meetings", "transcript", "TEXT"),
        ("meetings", "recording_url", "VARCHAR"),
        ("meetings", "transcript_url", "VARCHAR"),
        ("meetings", "mood", "VARCHAR"),
        ("meetings", "sentiment_score", "FLOAT"),
        ("meetings", "attendees", "JSONB"),
        ("meetings", "external_id", "VARCHAR"),
        # Checkups
        ("checkups", "client_id", "INTEGER REFERENCES clients(id)"),
        ("checkups", "merchrules_id", "VARCHAR"),
        ("checkups", "priority", "INTEGER DEFAULT 0"),
        ("checkups", "completed_date", "TIMESTAMP"),
        ("checkups", "scheduled_date", "TIMESTAMP"),
        ("checkups", "status", "VARCHAR"),
        ("checkups", "type", "VARCHAR"),
        # Users
        ("users", "role", "VARCHAR DEFAULT 'manager'"),
        ("users", "is_active", "BOOLEAN DEFAULT TRUE"),
        ("users", "hashed_password", "VARCHAR"),
        ("users", "telegram_id", "VARCHAR"),
        ("users", "updated_at", "TIMESTAMP"),
        # Accounts
        ("accounts", "domain", "VARCHAR"),
        ("accounts", "airtable_base_id", "VARCHAR"),
        ("accounts", "merchrules_login", "VARCHAR"),
        ("accounts", "is_active", "BOOLEAN DEFAULT TRUE"),
        ("accounts", "account_data", "JSONB"),
        # Audit logs
        ("audit_logs", "user_id", "INTEGER REFERENCES users(id)"),
        ("audit_logs", "ip_address", "VARCHAR"),
        ("audit_logs", "user_agent", "VARCHAR"),
        ("audit_logs", "old_values", "JSONB"),
        ("audit_logs", "new_values", "JSONB"),
        ("audit_logs", "resource_type", "VARCHAR"),
        ("audit_logs", "resource_id", "INTEGER"),
        ("audit_logs", "action", "VARCHAR"),
        # Notifications
        ("notifications", "user_id", "INTEGER REFERENCES users(id)"),
        ("notifications", "title", "VARCHAR"),
        ("notifications", "message", "TEXT"),
        ("notifications", "type", "VARCHAR"),
        ("notifications", "is_read", "BOOLEAN DEFAULT FALSE"),
        ("notifications", "related_resource_type", "VARCHAR"),
        ("notifications", "related_resource_id", "INTEGER"),
        ("notifications", "read_at", "TIMESTAMP"),
        # Sync logs
        ("sync_logs", "sync_data", "JSONB"),
    ]

    applied = 0
    for table, col, col_type in pending:
        existing = tables_columns.get(table, set())
        if col not in existing:
            try:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                db.commit()
                applied += 1
                logger.info(f"  + {table}.{col}")
            except Exception as e:
                logger.warning(f"  ! {table}.{col}: {e}")

    if applied:
        logger.info(f"✅ Applied {applied} column migrations")
    else:
        logger.info("✅ Schema up to date")


# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(
    title="AM Hub",
    description="Account Manager Hub - Complete platform",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=100)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ============================================================================
# HEALTH & ROOT
# ============================================================================

@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "websockets": manager.get_connection_count(),
    }


@app.get("/health/detailed")
async def health_detailed():
    """Detailed health check"""
    from monitoring import get_startup_checks
    return get_startup_checks()


@app.get("/health/integrations")
async def health_integrations():
    """Check integration status"""
    from monitoring import get_integration_status
    return get_integration_status()


@app.get("/metrics")
async def metrics():
    """Prometheus metrics"""
    from monitoring import metrics as metrics_collector
    return metrics_collector.get_prometheus_metrics()


@app.get("/api/health/metrics")
async def metrics_json():
    """JSON metrics"""
    from monitoring import metrics as metrics_collector
    return metrics_collector.get_health_metrics()


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, db: Session = Depends(get_db)):
    """Root page"""
    segment = request.query_params.get("segment")
    sort = request.query_params.get("sort")

    query = db.query(Client)
    clients = query.all()

    # Подсчёт по сегментам
    counts = {"ENT": 0, "SME+": 0, "SME-": 0, "SME": 0, "SMB": 0, "SS": 0}
    for c in clients:
        seg = c.segment or ""
        if seg in counts:
            counts[seg] += 1

    if segment:
        clients = [c for c in clients if c.segment == segment]

    # Обогащаем клиентов вычисляемыми полями для шаблона
    for c in clients:
        open_tasks = db.query(Task).filter(
            Task.client_id == c.id,
            Task.status.in_(["plan", "in_progress"])
        ).count()
        blocked_tasks = db.query(Task).filter(
            Task.client_id == c.id,
            Task.status == "blocked"
        ).count()

        # Определяем статус-цвет
        is_overdue = c.needs_checkup and (
            not c.last_meeting_date or
            (datetime.now() - c.last_meeting_date).days > 30
        )
        is_warning = c.needs_checkup and (
            c.last_meeting_date and
            14 < (datetime.now() - c.last_meeting_date).days <= 30
        )

        c.open_tasks = open_tasks
        c.blocked_tasks = blocked_tasks
        c.status = {
            "color": "red" if is_overdue else ("yellow" if is_warning else "green"),
            "next_date": None,
        }

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "clients": clients,
            "counts": counts,
            "segment": segment,
            "sort": sort,
        },
    )


# ============================================================================
# AUTH ENDPOINTS
# ============================================================================

@app.post("/api/auth/register", response_model=UserResponse)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    """Register new user"""
    try:
        # Check if user exists
        existing = db.query(User).filter(User.email == user.email).first()
        if existing:
            raise ConflictError("User with this email already exists")
        
        # Create user
        new_user = create_user(user, db)
        
        return UserResponse.from_orm(new_user)
    
    except ConflictError:
        raise
    except Exception as e:
        log_error(e, "register")
        raise BadRequestError(str(e))


@app.post("/api/auth/login")
async def login(email: str, password: str, db: Session = Depends(get_db)):
    """Login user"""
    try:
        user = authenticate_user(db, email, password)
        if not user:
            raise UnauthorizedError("Invalid email or password")
        
        token = create_access_token({"sub": str(user.id)})
        
        # Log audit
        await log_audit(
            db, user.id, "LOGIN", "User logged in",
            {"email": email}
        )
        
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": UserResponse.from_orm(user)
        }
    
    except UnauthorizedError:
        raise
    except Exception as e:
        log_error(e, "login")
        raise BadRequestError("Login failed")


# ============================================================================
# PROFILE ENDPOINTS
# ============================================================================

@app.get("/api/me", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserResponse.from_orm(current_user)


@app.put("/api/me")
async def update_profile(
    name: Optional[str] = None,
    phone: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update user profile"""
    try:
        if name:
            current_user.name = name
        if phone:
            current_user.phone = phone
        
        db.commit()
        
        await log_audit(
            db, current_user.id, "PROFILE_UPDATE",
            "User updated profile", {"name": name, "phone": phone}
        )
        
        return UserResponse.from_orm(current_user)
    
    except Exception as e:
        db.rollback()
        log_error(e, "update_profile")
        raise handle_db_error(e)


# ============================================================================
# CLIENTS ENDPOINTS
# ============================================================================

@app.get("/api/clients", response_model=PaginatedResponse)
async def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    segment: Optional[str] = None,
    manager_email: Optional[str] = None,
    health_min: Optional[float] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List clients with filters and pagination"""
    try:
        query = db.query(Client)
        
        # Фильтровать по доступу пользователя
        if current_user.role == "manager":
            query = query.filter(
                (Client.manager_email == current_user.email) |
                (Client.account_id == current_user.account_id)
            )
        elif current_user.role == "viewer":
            raise ForbiddenError("Viewers cannot list clients")
        
        # Применить фильтры
        if segment:
            query = query.filter(Client.segment == segment)
        
        if manager_email:
            query = query.filter(Client.manager_email == manager_email)
        
        if health_min is not None:
            query = query.filter(Client.health_score >= health_min)
        
        # Получить total count
        total = query.count()
        
        # Pagination
        clients = query.offset(skip).limit(limit).all()
        
        return PaginatedResponse(
            data=[ClientResponse.from_orm(c) for c in clients],
            total=total,
            skip=skip,
            limit=limit,
            has_more=skip + limit < total,
        )
    
    except ForbiddenError:
        raise
    except Exception as e:
        log_error(e, "list_clients")
        raise handle_db_error(e)


@app.post("/api/clients", response_model=ClientResponse, status_code=201)
async def create_client(
    client: ClientCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create new client"""
    try:
        # Валидировать данные
        validator = ClientValidator(
            name=client.name,
            email=client.email,
            phone=client.phone or "",
            segment=client.segment or "smb"
        )
        
        # Check for duplicates
        existing = db.query(Client).filter(Client.email == client.email).first()
        if existing:
            raise ConflictError(f"Client with email {client.email} already exists")
        
        # Create
        new_client = Client(
            name=client.name,
            email=client.email,
            phone=client.phone,
            segment=client.segment,
            manager_email=current_user.email,
            account_id=current_user.account_id,
            status="active",
            health_score=75,
            created_at=datetime.now(),
        )
        
        db.add(new_client)
        db.commit()
        db.refresh(new_client)
        
        # Audit
        await log_audit(
            db, current_user.id, "CLIENT_CREATE",
            f"Created client {new_client.name}",
            {"client_id": new_client.id}
        )
        
        # WebSocket notification
        await emit_task_created(current_user.id, {
            "id": new_client.id,
            "name": new_client.name,
        })
        
        # Email to team
        try:
            await send_task_created(
                current_user.email,
                f"New Client: {new_client.name}",
                new_client.name,
            )
        except:
            pass
        
        return ClientResponse.from_orm(new_client)
    
    except (ValidationError, ConflictError):
        raise
    except Exception as e:
        db.rollback()
        log_error(e, "create_client")
        raise handle_db_error(e)


@app.get("/api/clients/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get client details"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        # Check access
        ensure_client_access(current_user, client, db)
        
        return ClientResponse.from_orm(client)
    
    except ResourceNotFoundError:
        raise
    except ForbiddenError:
        raise
    except Exception as e:
        log_error(e, "get_client")
        raise handle_db_error(e)


@app.put("/api/clients/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: int,
    update: ClientUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update client"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        # Check access
        ensure_client_access(current_user, client, db)
        
        # Update fields
        update_data = update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(client, field, value)
        
        client.updated_at = datetime.now()
        db.commit()
        db.refresh(client)
        
        # Audit
        await log_audit(
            db, current_user.id, "CLIENT_UPDATE",
            f"Updated client {client.name}",
            {"client_id": client_id, "updates": update_data}
        )
        
        # WebSocket
        await emit_client_updated(current_user.id, client_id, update_data)
        
        return ClientResponse.from_orm(client)
    
    except (ResourceNotFoundError, ForbiddenError):
        raise
    except Exception as e:
        db.rollback()
        log_error(e, "update_client")
        raise handle_db_error(e)


@app.delete("/api/clients/{client_id}")
async def delete_client(
    client_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Delete client (admin only)"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        db.delete(client)
        db.commit()
        
        # Audit
        await log_audit(
            db, current_user.id, "CLIENT_DELETE",
            f"Deleted client {client.name}",
            {"client_id": client_id}
        )
        
        return {"status": "deleted"}
    
    except Exception as e:
        db.rollback()
        log_error(e, "delete_client")
        raise handle_db_error(e)


# ============================================================================
# TASKS ENDPOINTS
# ============================================================================

@app.get("/api/clients/{client_id}/tasks", response_model=PaginatedResponse)
async def list_tasks(
    client_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List tasks for client"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        # Check access
        ensure_client_access(current_user, client, db)
        
        query = db.query(Task).filter(Task.client_id == client_id)
        
        if status:
            query = query.filter(Task.status == status)
        
        if priority:
            query = query.filter(Task.priority == priority)
        
        total = query.count()
        tasks = query.offset(skip).limit(limit).all()
        
        return PaginatedResponse(
            data=[TaskResponse.from_orm(t) for t in tasks],
            total=total,
            skip=skip,
            limit=limit,
            has_more=skip + limit < total,
        )
    
    except ResourceNotFoundError:
        raise
    except ForbiddenError:
        raise
    except Exception as e:
        log_error(e, "list_tasks")
        raise handle_db_error(e)


@app.post("/api/clients/{client_id}/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    client_id: int,
    task: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create task for client"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        # Check access
        ensure_client_access(current_user, client, db)
        
        # Validate
        validator = TaskValidator(
            title=task.title,
            priority=task.priority,
            status=task.status,
            due_date=task.due_date,
        )
        
        new_task = Task(
            client_id=client_id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            due_date=task.due_date,
            source=task.source or "manual",
            created_at=datetime.now(),
        )
        
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        
        # Audit
        await log_audit(
            db, current_user.id, "TASK_CREATE",
            f"Created task {new_task.title}",
            {"task_id": new_task.id, "client_id": client_id}
        )
        
        # Email
        try:
            await send_task_created(current_user.email, new_task.title, client.name)
        except:
            pass
        
        return TaskResponse.from_orm(new_task)
    
    except (ResourceNotFoundError, ForbiddenError, ValidationError):
        raise
    except Exception as e:
        db.rollback()
        log_error(e, "create_task")
        raise handle_db_error(e)


# ============================================================================
# MEETINGS ENDPOINTS
# ============================================================================

@app.get("/api/clients/{client_id}/meetings", response_model=PaginatedResponse)
async def list_meetings(
    client_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    meeting_type: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List meetings for client"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        ensure_client_access(current_user, client, db)
        
        query = db.query(Meeting).filter(Meeting.client_id == client_id)
        
        if meeting_type:
            query = query.filter(Meeting.meeting_type == meeting_type)
        
        total = query.count()
        meetings = query.order_by(Meeting.meeting_date.desc()).offset(skip).limit(limit).all()
        
        return PaginatedResponse(
            data=[MeetingResponse.from_orm(m) for m in meetings],
            total=total,
            skip=skip,
            limit=limit,
            has_more=skip + limit < total,
        )
    
    except (ResourceNotFoundError, ForbiddenError):
        raise
    except Exception as e:
        log_error(e, "list_meetings")
        raise handle_db_error(e)


@app.post("/api/clients/{client_id}/meetings", response_model=MeetingResponse, status_code=201)
async def create_meeting(
    client_id: int,
    meeting: MeetingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create meeting for client"""
    try:
        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise ResourceNotFoundError("Client", client_id)
        
        ensure_client_access(current_user, client, db)
        
        # Validate
        validator = MeetingValidator(
            meeting_type=meeting.meeting_type,
            duration_minutes=meeting.duration_minutes,
            meeting_date=meeting.meeting_date,
        )
        
        new_meeting = Meeting(
            client_id=client_id,
            meeting_type=meeting.meeting_type,
            meeting_date=meeting.meeting_date,
            duration_minutes=meeting.duration_minutes,
            notes=meeting.notes,
            transcript=meeting.transcript,
            recording_url=meeting.recording_url,
            created_at=datetime.now(),
        )
        
        db.add(new_meeting)
        
        # Update client's last_meeting_date
        client.last_meeting_date = meeting.meeting_date
        
        db.commit()
        db.refresh(new_meeting)
        
        # Audit
        await log_audit(
            db, current_user.id, "MEETING_CREATE",
            f"Created {meeting.meeting_type} meeting",
            {"meeting_id": new_meeting.id, "client_id": client_id}
        )
        
        return MeetingResponse.from_orm(new_meeting)
    
    except (ResourceNotFoundError, ForbiddenError, ValidationError):
        raise
    except Exception as e:
        db.rollback()
        log_error(e, "create_meeting")
        raise handle_db_error(e)


# ============================================================================
# SYNC ENDPOINTS
# ============================================================================

@app.post("/api/sync/clients")
async def sync_clients(
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Sync clients from Airtable"""
    try:
        # Fetch from Airtable
        clients_data = await sync_clients_airtable()
        
        synced = 0
        for client_data in clients_data:
            existing = db.query(Client).filter(
                Client.email == client_data.get("email")
            ).first()
            
            if not existing:
                new_client = Client(
                    name=client_data.get("name"),
                    email=client_data.get("email"),
                    phone=client_data.get("phone"),
                    segment=client_data.get("segment", "smb"),
                    account_id=current_user.account_id,
                    status="active",
                    created_at=datetime.now(),
                )
                db.add(new_client)
                synced += 1
        
        db.commit()
        
        # Create sync log
        log = SyncLog(
            source="airtable",
            status="success",
            count=synced,
            user_id=current_user.id,
            created_at=datetime.now(),
        )
        db.add(log)
        db.commit()
        
        logger.info(f"✅ Synced {synced} clients from Airtable")
        
        return {"status": "success", "synced": synced}
    
    except Exception as e:
        db.rollback()
        log_error(e, "sync_clients")
        
        log = SyncLog(
            source="airtable",
            status="error",
            error_message=str(e),
            user_id=current_user.id,
            created_at=datetime.now(),
        )
        db.add(log)
        db.commit()
        
        raise BadRequestError(f"Sync error: {str(e)}")


# ============================================================================
# FILE UPLOAD/EXPORT
# ============================================================================

@app.post("/api/files/upload/clients")
async def upload_clients(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Upload and import clients from CSV/Excel"""
    try:
        content = await file.read()
        
        # Import
        success, msg, data = BulkImporter.import_clients(content, file.filename)
        
        if not success:
            raise ValidationError(msg)
        
        # Create clients
        created = 0
        for row in data:
            existing = db.query(Client).filter(
                Client.email == row.get("email")
            ).first()
            
            if not existing:
                new_client = Client(
                    name=row.get("name"),
                    email=row.get("email"),
                    phone=row.get("phone"),
                    segment=row.get("segment", "smb"),
                    account_id=current_user.account_id,
                    status="active",
                    created_at=datetime.now(),
                )
                db.add(new_client)
                created += 1
        
        db.commit()
        
        return {"status": "success", "created": created}
    
    except ValidationError:
        raise
    except Exception as e:
        db.rollback()
        log_error(e, "upload_clients")
        raise BadRequestError(str(e))


@app.get("/api/clients/export/{format}")
async def export_clients(
    format: str = "excel",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export clients to CSV/Excel/PDF"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        
        # Prepare data
        export_data = [
            {
                "name": c.name,
                "email": c.email,
                "phone": c.phone or "",
                "segment": c.segment,
                "health_score": c.health_score,
                "manager_name": c.manager_email,
            }
            for c in clients
        ]
        
        # Export
        if format == "csv":
            content = FileProcessor.to_csv(export_data)
            filename = f"clients_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif format == "pdf":
            content = FileProcessor.to_pdf(export_data, title="Clients")
            filename = f"clients_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        else:  # excel
            content = FileProcessor.to_excel(export_data, sheet_name="Clients")
            filename = f"clients_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return FileResponse(
            io.BytesIO(content),
            filename=filename,
            media_type="application/octet-stream"
        )
    
    except Exception as e:
        log_error(e, "export_clients")
        raise BadRequestError(str(e))


# ============================================================================
# WEBSOCKET
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    """WebSocket endpoint for real-time updates"""
    try:
        # Verify token and get user
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        
        # Simple token validation (implement full JWT validation in production)
        await handle_websocket_connection(websocket, None)  # In production, get user from token
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")


# ============================================================================
# STATS & ANALYTICS
# ============================================================================

@app.get("/api/stats")
async def get_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get dashboard statistics"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        
        total_clients = len(clients)
        avg_health = sum(c.health_score or 0 for c in clients) / total_clients if total_clients > 0 else 0
        
        # Tasks
        task_query = db.query(Task).filter(
            Task.client_id.in_([c.id for c in clients])
        )
        total_tasks = task_query.count()
        open_tasks = task_query.filter(Task.status == "open").count()
        
        # Meetings
        meeting_query = db.query(Meeting).filter(
            Meeting.client_id.in_([c.id for c in clients])
        )
        total_meetings = meeting_query.count()
        
        return {
            "total_clients": total_clients,
            "avg_health_score": round(avg_health, 2),
            "total_tasks": total_tasks,
            "open_tasks": open_tasks,
            "total_meetings": total_meetings,
            "websocket_connections": manager.get_connection_count(),
        }
    
    except Exception as e:
        log_error(e, "get_stats")
        return {}


# ============================================================================
# DASHBOARD ENDPOINTS
# ============================================================================

@app.get("/api/dashboard/clients-summary")
async def dashboard_clients_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed clients summary for dashboard"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        
        # Enrich clients with task and meeting counts
        result = []
        for client in clients:
            tasks_count = db.query(Task).filter(Task.client_id == client.id).count()
            meetings_count = db.query(Meeting).filter(Meeting.client_id == client.id).count()
            open_tasks = db.query(Task).filter(
                Task.client_id == client.id,
                Task.status.in_(["plan", "in_progress"])
            ).count()
            
            result.append({
                "id": client.id,
                "name": client.name,
                "email": client.email,
                "segment": client.segment,
                "health_score": client.health_score or 0,
                "manager_email": client.manager_email,
                "tasks_count": tasks_count,
                "open_tasks": open_tasks,
                "meetings_count": meetings_count,
                "last_meeting_date": client.last_meeting_date.isoformat() if client.last_meeting_date else None,
                "last_checkup": client.last_checkup.isoformat() if client.last_checkup else None,
            })
        
        return {"data": result, "total": len(result)}
    
    except Exception as e:
        log_error(e, "dashboard_clients_summary")
        raise BadRequestError(str(e))


@app.get("/api/dashboard/health-report")
async def dashboard_health_report(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get health status report for all clients"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        
        # Categorize by health
        critical = []  # < 50
        warning = []   # 50-75
        healthy = []   # 75+
        
        for client in clients:
            score = client.health_score or 0
            item = {
                "id": client.id,
                "name": client.name,
                "score": score,
                "segment": client.segment,
            }
            
            if score < 50:
                critical.append(item)
            elif score < 75:
                warning.append(item)
            else:
                healthy.append(item)
        
        return {
            "critical": critical,
            "warning": warning,
            "healthy": healthy,
            "summary": {
                "total": len(clients),
                "critical_count": len(critical),
                "warning_count": len(warning),
                "healthy_count": len(healthy),
                "avg_health": round(sum(c.health_score or 0 for c in clients) / len(clients), 2) if clients else 0,
            }
        }
    
    except Exception as e:
        log_error(e, "dashboard_health_report")
        raise BadRequestError(str(e))


@app.get("/api/dashboard/tasks-summary")
async def dashboard_tasks_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get tasks summary and breakdown"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        client_ids = [c.id for c in clients]
        
        # Get all tasks
        tasks_query = db.query(Task).filter(Task.client_id.in_(client_ids))
        
        # Count by status
        statuses = {}
        for status in ["plan", "in_progress", "blocked", "done"]:
            count = tasks_query.filter(Task.status == status).count()
            statuses[status] = count
        
        # Count by priority
        priorities = {}
        for priority in ["low", "medium", "high", "critical"]:
            count = tasks_query.filter(Task.priority == priority).count()
            priorities[priority] = count
        
        # Get recent tasks
        from sqlalchemy import desc
        recent_tasks = tasks_query.order_by(desc(Task.created_at)).limit(10).all()
        
        return {
            "total_tasks": tasks_query.count(),
            "by_status": statuses,
            "by_priority": priorities,
            "recent": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "priority": t.priority,
                    "client_id": t.client_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in recent_tasks
            ],
        }
    
    except Exception as e:
        log_error(e, "dashboard_tasks_summary")
        raise BadRequestError(str(e))


@app.get("/api/dashboard/timeline")
async def dashboard_timeline(
    current_user: User = Depends(get_current_user),
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Get timeline of recent activities"""
    try:
        query = db.query(Client)
        
        if current_user.role == "manager":
            query = query.filter(Client.manager_email == current_user.email)
        
        clients = query.all()
        client_ids = [c.id for c in clients]
        
        # Get recent meetings
        from datetime import timedelta
        from sqlalchemy import desc
        cutoff = datetime.now() - timedelta(days=days)
        
        meetings = db.query(Meeting).filter(
            Meeting.client_id.in_(client_ids),
            Meeting.meeting_date >= cutoff
        ).order_by(desc(Meeting.meeting_date)).limit(20).all()
        
        meetings_data = [
            {
                "type": "meeting",
                "title": f"{m.meeting_type} - {m.client.name}",
                "date": m.meeting_date.isoformat() if m.meeting_date else None,
                "client_id": m.client_id,
                "client_name": m.client.name,
            }
            for m in meetings
        ]
        
        # Get recent tasks
        tasks = db.query(Task).filter(
            Task.client_id.in_(client_ids),
            Task.created_at >= cutoff
        ).order_by(desc(Task.created_at)).limit(20).all()
        
        tasks_data = [
            {
                "type": "task",
                "title": f"{t.title} ({t.status})",
                "date": t.created_at.isoformat() if t.created_at else None,
                "client_id": t.client_id,
                "priority": t.priority,
            }
            for t in tasks
        ]
        
        # Merge and sort by date
        timeline = sorted(
            meetings_data + tasks_data,
            key=lambda x: x["date"],
            reverse=True
        )
        
        return {"timeline": timeline[:30]}
    
    except Exception as e:
        log_error(e, "dashboard_timeline")
        raise BadRequestError(str(e))


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail} if isinstance(exc.detail, str) else exc.detail,
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "dev") == "dev",
    )
