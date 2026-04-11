from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    domain = Column(String, nullable=True)
    segment = Column(String, default="SMB")
    manager_email = Column(String, nullable=True)
    site_ids = Column(JSON, default=list)
    health_score = Column(Float, default=50.0)
    last_checkup = Column(DateTime, nullable=True)
    revenue_trend = Column(String, default="stable") # growth, drop, stable
    open_tickets = Column(Integer, default=0)
    
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    title = Column(String)
    status = Column(String, default="todo") # todo, in_progress, done
    priority = Column(String, default="medium") # low, medium, high
    created_at = Column(DateTime, default=datetime.utcnow)

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"))
    date = Column(DateTime)
    summary = Column(String, nullable=True)
    type = Column(String, default="checkup")
