import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Получаем URL базы данных
DATABASE_URL = os.environ.get("DATABASE_URL")

# Если переменной нет, используем заглушку (чтобы приложение запустилось)
# Ошибка возникнет только при реальном запросе к БД
if not DATABASE_URL:
    print("⚠️ WARNING: DATABASE_URL not found. Using fallback. App will run but DB features disabled.")
    # Для локального теста можно использовать SQLite, но на Railway нужен Postgres
    # DATABASE_URL = "sqlite:///./test.db" 
    engine = None
    SessionLocal = None
    Base = declarative_base()
else:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    try:
        engine = create_engine(DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base = declarative_base()
        print("✅ Database engine created successfully.")
    except Exception as e:
        print(f"❌ Error creating DB engine: {e}")
        engine = None
        SessionLocal = None
        Base = declarative_base()

def get_db():
    if SessionLocal is None:
        raise Exception("Database not configured. Check DATABASE_URL.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    if engine:
        try:
            Base.metadata.create_all(bind=engine)
            print("✅ Database tables created.")
        except Exception as e:
            print(f"❌ Error creating tables: {e}")
    else:
        print("⚠️ Skipping table creation (no engine).")
