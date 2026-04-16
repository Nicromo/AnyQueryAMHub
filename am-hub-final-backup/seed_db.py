#!/usr/bin/env python3
"""
Seed database with sample data for testing and demo.
Usage: python3 seed_db.py
"""
import os
import random
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, SessionLocal

# Override engine if DATABASE_URL is set
import os
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(_db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
from models import Client, Task, Meeting, CheckUp, User, Account

# Sample data
SEGMENTS = ["ENT", "SME+", "SME-", "SMB", "SS"]
COMPANIES_ENT = ["Сбербанк", "Яндекс", "МТС", "Ростелеком", "Тинькофф"]
COMPANIES_SME_PLUS = ["Ozon", "Wildberries", "Lamoda", "DNS", "М.Видео"]
COMPANIES_SME_MINUS = ["Ситилинк", "Эльдорадо", "Эксперт", "Поларис", "Беру"]
COMPANIES_SMB = ["Магазин у дома", "Кофейня №1", "Студия красоты", "Фитнес-клуб", "Автосервис"]
COMPANIES_SS = ["ИП Иванов", "ИП Петров", "ИП Сидоров", "ИП Козлов", "ИП Новиков"]

MANAGERS = ["ivan@company.ru", "maria@company.ru", "alex@company.ru", "elena@company.ru"]

TASK_STATUSES = ["plan", "in_progress", "done", "blocked", "discussion"]
TASK_PRIORITIES = ["low", "medium", "high", "critical"]
TASK_TYPES = ["search_quality", "analytics", "tracking", "merchandising", "data_science", "rnd"]
TEAMS = ["LINGUISTS", "ANALYTICS", "TRACKING", "DEV", "BACKEND", "DATASCI", "CS"]

MEETING_TYPES = ["checkup", "qbr", "kickoff", "sync"]
MEETING_MOODS = ["positive", "neutral", "negative"]

CHECKUP_STATUSES = ["overdue", "scheduled", "completed", "cancelled"]


def seed_users(db):
    """Create sample users"""
    if db.query(User).count() > 0:
        print("  Users already exist, skipping...")
        return

    users_data = [
        {"email": "admin@company.ru", "first_name": "Админ", "last_name": "Системный", "role": "admin", "telegram_id": None},
        {"email": "ivan@company.ru", "first_name": "Иван", "last_name": "Менеджер", "role": "manager", "telegram_id": "123456"},
        {"email": "maria@company.ru", "first_name": "Мария", "last_name": "Аналитик", "role": "manager", "telegram_id": "789012"},
        {"email": "viewer@company.ru", "first_name": "Зритель", "last_name": "Только чтение", "role": "viewer", "telegram_id": None},
    ]

    for ud in users_data:
        user = User(**ud)
        if ud["role"] != "viewer":
            user.hashed_password = "$2b$12$LJ3m4ys3Lk7YqF8xGz9vOuP5R6tN2wQ1sA4bC7dE8fG9hI0jK1lM2"  # 'password123'
        db.add(user)

    db.commit()
    print(f"  ✅ Created {len(users_data)} users")


def seed_account(db):
    """Create default account"""
    if db.query(Account).count() > 0:
        print("  Account already exists, skipping...")
        return

    account = Account(
        name="Demo Account",
        domain="company.ru",
        is_active=True,
    )
    db.add(account)
    db.commit()
    print("  ✅ Created default account")


def seed_clients(db):
    """Create sample clients"""
    if db.query(Client).count() > 0:
        print("  Clients already exist, skipping...")
        return

    account = db.query(Account).first()

    companies_by_segment = {
        "ENT": COMPANIES_ENT,
        "SME+": COMPANIES_SME_PLUS,
        "SME-": COMPANIES_SME_MINUS,
        "SMB": COMPANIES_SMB,
        "SS": COMPANIES_SS,
    }

    domains_pool = ["sber.ru", "yandex.ru", "mts.ru", "rt.ru", "tinkoff.ru",
                    "ozon.ru", "wb.ru", "lamoda.ru", "dns-shop.ru", "mvideo.ru",
                    "citilink.ru", "eldorado.ru", "expert.ru", "polaris.ru", "beru.ru"]

    clients = []
    idx = 0
    for segment, companies in companies_by_segment.items():
        for name in companies:
            domain = domains_pool[idx % len(domains_pool)]
            client = Client(
                name=name,
                domain=domain,
                segment=segment,
                manager_email=random.choice(MANAGERS),
                account_id=account.id if account else None,
                health_score=round(random.uniform(0.3, 1.0), 2),
                activity_level=random.choice(["high", "medium", "low"]),
                open_tickets=random.randint(0, 5),
                site_ids=[random.randint(100, 9999)],
                last_meeting_date=datetime.now() - timedelta(days=random.randint(1, 60)),
                last_checkup=datetime.now() - timedelta(days=random.randint(1, 90)),
                needs_checkup=random.choice([True, False]),
                revenue_trend=random.choice(["growing", "stable", "declining"]),
            )
            db.add(client)
            clients.append(client)
            idx += 1

    db.commit()
    print(f"  ✅ Created {len(clients)} clients")
    return clients


def seed_tasks(db, clients):
    """Create sample tasks"""
    if db.query(Task).count() > 0:
        print("  Tasks already exist, skipping...")
        return

    task_titles = [
        "Настроить трекинг событий",
        "Проверить качество поиска",
        "Интегрировать API рекомендаций",
        "Обновить модель ранжирования",
        "Провести A/B тест",
        "Оптимизировать выдачу",
        "Добавить новые фильтры",
        "Настроить персонализацию",
        "Проверить релевантность",
        "Обновить синонимы",
        "Настроить мерчандайзинг",
        "Проверить конверсию",
        "Анализировать метрики",
        "Настроить дашборд",
        "Исследовать поведение пользователей",
    ]

    tasks = []
    for client in clients:
        num_tasks = random.randint(2, 8)
        for _ in range(num_tasks):
            status = random.choice(TASK_STATUSES)
            task = Task(
                client_id=client.id,
                title=random.choice(task_titles),
                description=f"Задача для клиента {client.name}. Требуется выполнить в рамках текущего квартала.",
                status=status,
                priority=random.choice(TASK_PRIORITIES),
                team=random.choice(TEAMS) if random.random() > 0.3 else "",
                task_type=random.choice(TASK_TYPES) if random.random() > 0.3 else "",
                source=random.choice(["manual", "roadmap", "checkup", "auto"]),
                created_at=datetime.now() - timedelta(days=random.randint(1, 30)),
                due_date=datetime.now() + timedelta(days=random.randint(-5, 30)),
            )
            db.add(task)
            tasks.append(task)

    db.commit()
    print(f"  ✅ Created {len(tasks)} tasks")


def seed_meetings(db, clients):
    """Create sample meetings"""
    if db.query(Meeting).count() > 0:
        print("  Meetings already exist, skipping...")
        return

    meetings = []
    for client in clients[:10]:  # Только для первых 10 клиентов
        num_meetings = random.randint(1, 3)
        for _ in range(num_meetings):
            meeting = Meeting(
                client_id=client.id,
                date=datetime.now() - timedelta(days=random.randint(1, 90)),
                type=random.choice(MEETING_TYPES),
                title=f"Встреча с {client.name}",
                summary="Обсудили текущие задачи и планы на следующий квартал. Клиент доволен результатами.",
                mood=random.choice(MEETING_MOODS),
                sentiment_score=round(random.uniform(0.5, 1.0), 2),
                attendees=[{"name": random.choice(MANAGERS), "role": "manager"},
                           {"name": f"contact@{client.domain}", "role": "client"}],
            )
            db.add(meeting)
            meetings.append(meeting)

    db.commit()
    print(f"  ✅ Created {len(meetings)} meetings")


def seed_checkups(db, clients):
    """Create sample checkups"""
    if db.query(CheckUp).count() > 0:
        print("  Checkups already exist, skipping...")
        return

    checkups = []
    for client in clients[:8]:
        checkup = CheckUp(
            client_id=client.id,
            type=random.choice(["quarterly", "annual", "monthly"]),
            status=random.choice(CHECKUP_STATUSES),
            scheduled_date=datetime.now() + timedelta(days=random.randint(-10, 30)),
            priority=random.randint(1, 10),
        )
        db.add(checkup)
        checkups.append(checkup)

    db.commit()
    print(f"  ✅ Created {len(checkups)} checkups")


def main():
    print("🌱 Seeding database...")

    db = SessionLocal()
    try:
        seed_users(db)
        seed_account(db)
        clients = seed_clients(db)
        if clients:
            seed_tasks(db, clients)
            seed_meetings(db, clients)
            seed_checkups(db, clients)
        print("\n✅ Database seeded successfully!")
    except Exception as e:
        db.rollback()
        print(f"\n❌ Error seeding database: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
