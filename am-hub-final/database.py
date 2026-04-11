import os
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    # Если нет URL, создаем заглушку для локального теста (но на Railway она будет)
    print("⚠️ WARNING: DATABASE_URL not found. App might fail if DB is required.")
    DATABASE_URL = "postgresql://postgres:password@localhost:5432/amhub"

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
except Exception as e:
    print(f"❌ DB Connection Error: {e}")
    raise

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created/verified successfully.")
    except Exception as e:
        print(f"⚠️ Table creation warning: {e}")
