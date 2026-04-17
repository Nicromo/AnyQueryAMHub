"""
База данных — SQLite через встроенный sqlite3.
Никаких ORM — просто, быстро, надёжно.
"""
import sqlite3
import json
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "am_hub.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS clients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            segment     TEXT NOT NULL CHECK(segment IN ('ENT','SME','SMB','SS')),
            tg_chat_id  TEXT,          -- chat_id канала клиента в TG
            last_checkup DATE,         -- дата последней встречи
            notes       TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS meetings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            meeting_date    DATE NOT NULL,
            meeting_type    TEXT DEFAULT 'checkup', -- checkup | qbr | urgent
            summary         TEXT DEFAULT '',
            mood            TEXT DEFAULT 'neutral', -- positive | neutral | risk
            next_meeting    DATE,
            tg_sent         INTEGER DEFAULT 0,      -- 1 = отправлено в TG
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id  INTEGER REFERENCES meetings(id),
            client_id   INTEGER NOT NULL REFERENCES clients(id),
            owner       TEXT NOT NULL CHECK(owner IN ('anyquery','client')),
            text        TEXT NOT NULL,
            due_date    DATE,
            status      TEXT DEFAULT 'open' CHECK(status IN ('open','done','blocked')),
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            tg_id       INTEGER PRIMARY KEY,
            tg_username TEXT,
            tg_name     TEXT,
            last_login  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)


# ── Clients ──────────────────────────────────────────────────────────────────

CHECKUP_DAYS = {"ENT": 30, "SME": 60, "SMB": 90, "SS": 90}


def get_all_clients():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*,
                   MAX(m.meeting_date) as last_meeting,
                   COUNT(t.id) as open_tasks
            FROM clients c
            LEFT JOIN meetings m ON m.client_id = c.id
            LEFT JOIN tasks t ON t.client_id = c.id AND t.status = 'open'
            GROUP BY c.id
            ORDER BY c.segment, c.name
        """).fetchall()
    return [dict(r) for r in rows]


def get_client(client_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return dict(row) if row else None


def upsert_client(name: str, segment: str, tg_chat_id: str = "", notes: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO clients (name, segment, tg_chat_id, notes)
               VALUES (?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 segment=excluded.segment,
                 tg_chat_id=excluded.tg_chat_id,
                 notes=excluded.notes
               RETURNING id""",
            (name, segment, tg_chat_id, notes)
        )
        return cur.fetchone()[0]


# ── Meetings ─────────────────────────────────────────────────────────────────

def get_client_meetings(client_id: int, limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM meetings WHERE client_id=? ORDER BY meeting_date DESC LIMIT ?",
            (client_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_meeting(meeting_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
    return dict(row) if row else None


def create_meeting(client_id: int, meeting_date: str, meeting_type: str,
                   summary: str, mood: str, next_meeting: str | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO meetings (client_id, meeting_date, meeting_type, summary, mood, next_meeting)
               VALUES (?,?,?,?,?,?) RETURNING id""",
            (client_id, meeting_date, meeting_type, summary, mood, next_meeting)
        )
        meeting_id = cur.fetchone()[0]
        # Обновляем last_checkup у клиента
        conn.execute(
            "UPDATE clients SET last_checkup=? WHERE id=?",
            (meeting_date, client_id)
        )
    return meeting_id


def mark_meeting_tg_sent(meeting_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE meetings SET tg_sent=1 WHERE id=?", (meeting_id,))


# ── Tasks ────────────────────────────────────────────────────────────────────

def get_client_tasks(client_id: int, status: str = "open"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT t.*, m.meeting_date FROM tasks t LEFT JOIN meetings m ON m.id=t.meeting_id "
            "WHERE t.client_id=? AND t.status=? ORDER BY t.due_date",
            (client_id, status)
        ).fetchall()
    return [dict(r) for r in rows]


def create_tasks_bulk(meeting_id: int, client_id: int, tasks: list[dict]):
    """tasks = [{"owner": "anyquery"|"client", "text": "...", "due_date": "YYYY-MM-DD"}]"""
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO tasks (meeting_id, client_id, owner, text, due_date) VALUES (?,?,?,?,?)",
            [(meeting_id, client_id, t["owner"], t["text"], t.get("due_date")) for t in tasks]
        )


def update_task_status(task_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
