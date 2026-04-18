import os
from pathlib import Path as _Path

# Load .env from the am-hub-final/ directory (same level as this file)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost/db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ── Connection pool ────────────────────────────────────────────────────────
# 4 workers × до 5 соединений = 20 max, +10 overflow = 30 всего
# Railway PostgreSQL держит до 100 connections по умолчанию
engine = create_engine(
    DATABASE_URL,
    pool_size=5,             # connections held in pool
    max_overflow=10,         # extra connections allowed beyond pool_size
    pool_pre_ping=True,      # авто-реконнект если соединение упало
    pool_recycle=300,        # recycle connections every 5 min (prevents stale connections)
    pool_timeout=30,         # ждать свободное соединение 30 сек
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"DB Warning: {e}")
