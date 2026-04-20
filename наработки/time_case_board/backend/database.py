from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    username: Mapped[str] = mapped_column(String(255), default="")


class KanbanColumn(Base):
    __tablename__ = "kanban_columns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False, default="tinkoff")
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), default="")
    channels_json: Mapped[str] = mapped_column(Text, default="")  # JSON: [{team_name, channel_name, channel_id?}, ...]
    position: Mapped[int] = mapped_column(Integer, default=0)
    rules_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cases: Mapped[list["Case"]] = relationship("Case", back_populates="column", cascade="all, delete-orphan")

    def rules(self) -> dict:
        try:
            r = json.loads(self.rules_json) or {}
        except json.JSONDecodeError:
            r = {}
        t = r.get("templates")
        # Старый дефолт был только support — AMA-треды отфильтровывались полностью
        if isinstance(t, list) and len(t) == 1 and t[0] == "support":
            out = dict(r)
            out["templates"] = ["support", "ds_ama"]
            return out
        return r


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    column_id: Mapped[str] = mapped_column(String(36), ForeignKey("kanban_columns.id", ondelete="CASCADE"))
    root_post_id: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), default="tinkoff")
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | resolved
    initiator: Mapped[str] = mapped_column(String(32), default="unspecified")  # self | incoming | unspecified
    root_author_user_id: Mapped[str] = mapped_column(String(64), default="")  # user_id автора корневого поста в TiMe
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assignee_raw: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title_preview: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    permalink: Mapped[str] = mapped_column(String(1024), default="")
    thread_search_text: Mapped[str] = mapped_column(Text, default="")
    last_activity_at_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_message_user_id: Mapped[str] = mapped_column(String(64), default="")
    last_message_username: Mapped[str] = mapped_column(String(255), default="")
    last_message_preview: Mapped[str] = mapped_column(String(1024), default="")
    raw_root_message: Mapped[str] = mapped_column(Text, default="")
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    column: Mapped["KanbanColumn"] = relationship("KanbanColumn", back_populates="cases")

    __table_args__ = (UniqueConstraint("channel_id", "root_post_id", name="uq_case_channel_root"),)


class IgnoredThread(Base):
    __tablename__ = "ignored_threads"
    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_post_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    channel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    since_ms: Mapped[int] = mapped_column(Integer, default=0)


class NotificationSettings(Base):
    __tablename__ = "notification_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time_channel_url: Mapped[str] = mapped_column(Text, default="")
    time_channel_id: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_new_comments: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=180)


class JiraWatcher(Base):
    __tablename__ = "jira_watchers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    label: Mapped[str] = mapped_column(String(255), default="")
    jql: Mapped[str] = mapped_column(Text, default="")
    watcher_type: Mapped[str] = mapped_column(String(32), default="custom")  # reporter|assignee|watcher|custom
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    states: Mapped[list["JiraNotifyState"]] = relationship(
        "JiraNotifyState", back_populates="watcher", cascade="all, delete-orphan"
    )


class JiraNotifyState(Base):
    """Tracks last-seen comment per (issue, watcher) to avoid re-notifying."""
    __tablename__ = "jira_notify_state"
    issue_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    watcher_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jira_watchers.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_comment_created: Mapped[str] = mapped_column(String(64), default="")

    watcher: Mapped["JiraWatcher"] = relationship("JiraWatcher", back_populates="states")


_engines: dict[str, object] = {}
_sessionmakers: dict[str, sessionmaker] = {}


def clear_engine_cache() -> None:
    global _engines, _sessionmakers
    for e in _engines.values():
        try:
            e.dispose()
        except Exception:
            pass
    _engines.clear()
    _sessionmakers.clear()


def get_engine(db_path: Path) -> object:
    key = str(db_path.resolve())
    if key not in _engines:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        _sqlite_migrations(eng)
        _engines[key] = eng
    return _engines[key]


def get_sessionmaker(db_path: Path) -> sessionmaker:
    key = str(db_path.resolve())
    if key not in _sessionmakers:
        _sessionmakers[key] = sessionmaker(
            bind=get_engine(db_path), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _sessionmakers[key]


def _sqlite_migrations(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        v = conn.execute(text("PRAGMA user_version")).scalar()
        v = int(v or 0)
        if v < 2:
            conn.execute(
                text("UPDATE cases SET initiator = 'unspecified' WHERE initiator = 'incoming' AND manual = 0")
            )
            conn.execute(text("PRAGMA user_version = 2"))
            v = 2
        if v < 3:
            try:
                conn.execute(text("ALTER TABLE kanban_columns ADD COLUMN channels_json TEXT NOT NULL DEFAULT ''"))
            except Exception:
                pass
            conn.execute(text("PRAGMA user_version = 3"))
            v = 3
        if v < 4:
            try:
                conn.execute(text("ALTER TABLE cases ADD COLUMN root_author_user_id VARCHAR(64) NOT NULL DEFAULT ''"))
            except Exception:
                pass
            conn.execute(text("PRAGMA user_version = 4"))
            v = 4
        if v < 5:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS notification_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time_channel_url TEXT NOT NULL DEFAULT '',
                    time_channel_id VARCHAR(64) NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    notify_new_comments INTEGER NOT NULL DEFAULT 1,
                    poll_interval_sec INTEGER NOT NULL DEFAULT 180
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS jira_watchers (
                    id VARCHAR(36) PRIMARY KEY,
                    label VARCHAR(255) NOT NULL DEFAULT '',
                    jql TEXT NOT NULL DEFAULT '',
                    watcher_type VARCHAR(32) NOT NULL DEFAULT 'custom',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS jira_notify_state (
                    issue_key VARCHAR(64) NOT NULL,
                    watcher_id VARCHAR(36) NOT NULL,
                    last_seen_comment_created VARCHAR(64) NOT NULL DEFAULT '',
                    PRIMARY KEY (issue_key, watcher_id),
                    FOREIGN KEY (watcher_id) REFERENCES jira_watchers(id) ON DELETE CASCADE
                )
            """))
            conn.execute(text("PRAGMA user_version = 5"))


def _ensure_profile_row_for_pat(db) -> None:
    """Для PAT в oauth.env пишем user_id/username в БД доски (токен не храним)."""
    from backend.config import settings

    if not (settings.time_personal_access_token or "").strip():
        return
    from backend.accounts import get_me_for_personal_token
    from backend.auth_tokens import get_stored_token_row

    try:
        me = get_me_for_personal_token()
        if not me:
            return
        uid = str(me.get("id") or "")
        un = str(me.get("username") or "")
        row = get_stored_token_row(db)
        if row and row.user_id == uid and (row.username or "") == un and uid:
            return
        if not row:
            row = OAuthToken()
            db.add(row)
        if uid:
            row.user_id = uid
        if un:
            row.username = un
        db.commit()
    except Exception:
        db.rollback()


def get_db():
    from backend.accounts import board_db_path, resolve_active_board_account_id

    aid = resolve_active_board_account_id()
    if not aid:
        raise HTTPException(
            status_code=401,
            detail="Нет активной учётки TiMe: войдите через OAuth или задайте TIME_PERSONAL_ACCESS_TOKEN в oauth.env.",
        )
    path = board_db_path(aid)
    SessionLocal = get_sessionmaker(path)
    db = SessionLocal()
    _ensure_profile_row_for_pat(db)
    try:
        yield db
    finally:
        db.close()
