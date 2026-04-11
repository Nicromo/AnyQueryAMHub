import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, JSON, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL) if DATABASE_URL else None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine else None
Base = declarative_base()

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, default="SMB")
    manager_id = Column(Integer, nullable=True)
    health_score = Column(Float, default=50.0)
    last_checkup = Column(DateTime, nullable=True)
    site_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    tasks = relationship("Task", back_populates="client", cascade="all, delete-orphan")
    meetings = relationship("Meeting", back_populates="client", cascade="all, delete-orphan")

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    title = Column(String)
    status = Column(String, default="todo") # todo, in_progress, done
    priority = Column(String, default="medium") # low, medium, high, critical
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    client = relationship("Client", back_populates="tasks")

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    date = Column(DateTime)
    summary = Column(Text, nullable=True)
    type = Column(String, default="checkup") # checkup, qbr, urgent
    client = relationship("Client", back_populates="meetings")

def get_db():
    if not SessionLocal:
        raise ValueError("Database not initialized. Check DATABASE_URL.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    if Base and engine:
        Base.metadata.create_all(bind=engine)
