import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Получаем URL базы данных из переменных окружения
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No DATABASE_URL found in environment variables. Check Railway settings.")

# Исправляем префикс для SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Создаем движок
engine = create_engine(DATABASE_URL)

# Сессия
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Базовый класс для моделей
Base = declarative_base()

# Зависимость для получения сессии БД
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Функция инициализации таблиц (вызывается при старте)
def init_db():
    Base.metadata.create_all(bind=engine)
