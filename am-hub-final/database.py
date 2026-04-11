import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ARRAY, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Если нет БД, используем заглушку (для локального теста без краша)
    print("⚠️ WARNING: DATABASE_URL not found. Using fallback.")
    DATABASE_URL = "sqlite:///fallback.db" 

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Client(Base):
    __tablename__ = 'clients'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, nullable=True) # ENT, SME, SMB
    manager_email = Column(String, nullable=True) # ИСПРАВЛЕНО: добавлено поле
    site_ids = Column(String, nullable=True) # Храним как строку "123,456"
    health_score = Column(Float, default=50.0)
    last_checkup = Column(DateTime, nullable=True)
    revenue_trend = Column(String, nullable=True)
    open_tickets = Column(Integer, default=0)
    
    def __repr__(self):
        return f"<Client(name='{self.name}', segment='{self.segment}')>"

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=True)
    title = Column(String)
    status = Column(String, default="todo") # todo, done
    priority = Column(String, default="medium")

def init_db():
    """Создает таблицы и добавляет недостающие колонки"""
    # 1. Создаем таблицы, если их нет
    Base.metadata.create_all(bind=engine)
    
    # 2. Проверяем наличие колонки manager_email (Миграция)
    if DATABASE_URL and "postgresql" in DATABASE_URL:
        try:
            inspector = inspect(engine)
            columns = [col['name'] for col in inspector.get_columns('clients')]
            
            if 'manager_email' not in columns:
                print("🔧 Adding missing column 'manager_email' to clients table...")
                with engine.connect() as conn:
                    conn.execute("ALTER TABLE clients ADD COLUMN manager_email VARCHAR;")
                    conn.commit()
                print("✅ Column added successfully.")
            else:
                print("✅ Database schema is up to date.")
                
            if 'site_ids' not in columns:
                print("🔧 Adding missing column 'site_ids'...")
                with engine.connect() as conn:
                    conn.execute("ALTER TABLE clients ADD COLUMN site_ids VARCHAR;")
                    conn.commit()
        except Exception as e:
            print(f"⚠️ Migration check failed (might be permissions): {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
