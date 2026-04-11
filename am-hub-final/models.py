from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, nullable=True)
    manager_email = Column(String, nullable=True) # Исправлено: добавлено поле
    site_ids = Column(JSONB, default=list)
    health_score = Column(Float, default=0.0)
    last_checkup = Column(DateTime, nullable=True)
    revenue_trend = Column(String, nullable=True)
    open_tickets = Column(Integer, default=0)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    title = Column(String, nullable=False)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
