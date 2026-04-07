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
            segment     TEXT NOT NULL CHECK(segment IN ('ENT','SME','SME+','SME-','SMB','SS')),
            site_ids    TEXT DEFAULT '',  -- через запятую, для Merchrules
            tg_chat_id  TEXT,             -- chat_id канала клиента в TG
            last_checkup DATE,            -- дата последней встречи
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
            mr_synced       INTEGER DEFAULT 0,      -- 1 = синхронизировано с Merchrules
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

        -- Миграция: добавить site_ids если не существует (для совместимости)
        """)
        # Миграции (безопасные — добавляют колонки если их нет)
        for col, defn in [
            ("site_ids", "TEXT DEFAULT ''"),
            ("mr_synced", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {defn}")
            except Exception:
                pass
            try:
                conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {defn}")
            except Exception:
                pass


# ── Clients ──────────────────────────────────────────────────────────────────

CHECKUP_DAYS = {"ENT": 30, "SME": 60, "SME+": 60, "SME-": 60, "SMB": 90, "SS": 90}


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
            ORDER BY CASE c.segment WHEN 'ENT' THEN 1 WHEN 'SME+' THEN 2
                     WHEN 'SME' THEN 3 WHEN 'SME-' THEN 4
                     WHEN 'SMB' THEN 5 ELSE 6 END, c.name
        """).fetchall()
    return [dict(r) for r in rows]


def get_client(client_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    return dict(row) if row else None


def upsert_client(name: str, segment: str, tg_chat_id: str = "",
                  notes: str = "", site_ids: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO clients (name, segment, tg_chat_id, notes, site_ids)
               VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 segment=excluded.segment,
                 tg_chat_id=excluded.tg_chat_id,
                 notes=excluded.notes,
                 site_ids=CASE WHEN excluded.site_ids != '' THEN excluded.site_ids
                               ELSE clients.site_ids END
               RETURNING id""",
            (name, segment, tg_chat_id, notes, site_ids)
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


def get_all_tasks(status: str = "open"):
    """Все задачи по всем клиентам с именем клиента."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, c.name as client_name, c.segment,
                      m.meeting_date
               FROM tasks t
               JOIN clients c ON c.id = t.client_id
               LEFT JOIN meetings m ON m.id = t.meeting_id
               WHERE t.status = ?
               ORDER BY COALESCE(t.due_date, '9999-12-31'), c.name""",
            (status,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_today_overview():
    """Данные для страницы 'Сегодня': просроченные чекапы + задачи с дедлайном сегодня/завтра."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    week_end = (date.today() + timedelta(days=7)).isoformat()

    with get_conn() as conn:
        # Задачи с дедлайном сегодня или просроченные
        urgent_tasks = conn.execute(
            """SELECT t.*, c.name as client_name, c.segment, c.id as client_id
               FROM tasks t JOIN clients c ON c.id = t.client_id
               WHERE t.status IN ('open','blocked') AND t.due_date <= ?
               ORDER BY t.due_date, c.name""",
            (today,)
        ).fetchall()

        # Задачи с дедлайном на этой неделе
        week_tasks = conn.execute(
            """SELECT t.*, c.name as client_name, c.segment, c.id as client_id
               FROM tasks t JOIN clients c ON c.id = t.client_id
               WHERE t.status IN ('open','blocked') AND t.due_date > ? AND t.due_date <= ?
               ORDER BY t.due_date, c.name""",
            (today, week_end)
        ).fetchall()

    return {
        "urgent_tasks": [dict(r) for r in urgent_tasks],
        "week_tasks": [dict(r) for r in week_tasks],
    }


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


# ── Seed data — твои клиенты ─────────────────────────────────────────────────

# Формат: (name, segment, site_ids)
# site_ids — через запятую, для Merchrules API
SEED_CLIENTS = [
    # ── ENT ──────────────────────────────────────────────────────────────────
    ("cdek.shopping",         "ENT",  "8591"),
    ("shoppinglive.ru",       "ENT",  "2878"),
    ("beeline.ru",            "ENT",  "2203,6784"),
    ("lazurit.com",           "ENT",  "2169"),
    ("m-market.kg",           "ENT",  "8875"),
    ("tts.ru",                "ENT",  "7085"),
    ("electrogor.ru",         "ENT",  "7093"),
    ("mechta.kz",             "ENT",  "2053"),
    ("vamsvet",               "ENT",  "1193"),
    ("kuvalda.ru",            "ENT",  "2557"),
    ("yves-rocher",           "ENT",  "513"),
    ("водолей.рф",            "ENT",  "2987"),
    ("tvoydom.ru",            "ENT",  "1462,7655"),
    ("ogo1.ru",               "ENT",  "662"),
    ("mila.by",               "ENT",  "8990"),
    # ── SME+ ─────────────────────────────────────────────────────────────────
    ("etm.ru",                "SME+", "2034"),
    ("dogeat.ru",             "SME+", "1951"),
    ("online-samsung.ru",     "SME+", "2199"),
    ("prezident.ru",          "SME+", "9261"),
    # ── SME- ─────────────────────────────────────────────────────────────────
    ("teremonline.ru",        "SME-", "1909"),
    ("ya-magazin",            "SME-", "9466"),
    ("monamiprofessional",    "SME-", "3982"),
    ("vodovoz.ru",            "SME-", "4882"),
    # ── SMB ──────────────────────────────────────────────────────────────────
    ("krasotkapro.ru",        "SMB",  "2485"),
    ("Semicvetic",            "SMB",  "2760"),
    ("postmeridiem",          "SMB",  "2284"),
    ("3259404.ru",            "SMB",  "2806"),
    ("fabrika-stil.ru",       "SMB",  "8499"),
    ("urbantiger",            "SMB",  "3811"),
    ("uyutstroy.su",          "SMB",  "3554"),
    ("divanboss.ru",          "SMB",  "7820"),
    ("Neverlate-shop",        "SMB",  "3080"),
    ("stout.ru",              "SMB",  "7596"),
    ("rommer.ru",             "SMB",  "7595"),
    # ── SS ───────────────────────────────────────────────────────────────────
    ("swankystamping",        "SS",   "3081"),
    ("teremoot",              "SS",   "5535"),
]


def seed_clients():
    """Заполнить/обновить БД клиентами из актуального списка."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    # Всегда обновляем site_ids даже если клиенты уже есть
    for name, segment, site_ids in SEED_CLIENTS:
        upsert_client(name, segment, site_ids=site_ids)
