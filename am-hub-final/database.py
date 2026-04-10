"""
База данных — SQLite через встроенный sqlite3.
Никаких ORM — просто, быстро, надёжно.
"""
import sqlite3
import json
from datetime import date, datetime, timedelta
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
            is_internal INTEGER DEFAULT 0,  -- 1 = внутренняя задача, не уходит в MR
            internal_note TEXT DEFAULT '',   -- комментарий/контекст от руководителя
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            tg_id       INTEGER PRIMARY KEY,
            tg_username TEXT,
            tg_name     TEXT,
            last_login  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS checklist_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL REFERENCES clients(id),
            meeting_id  INTEGER REFERENCES meetings(id),
            item_type   TEXT DEFAULT 'template', -- template | task | custom
            text        TEXT NOT NULL,
            hint        TEXT DEFAULT '',         -- подсказка для ИИ / что спросить
            checked     INTEGER DEFAULT 0,
            task_id     INTEGER,
            sort_order  INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Миграции (безопасные — добавляют колонки если их нет)
        for col, defn in [
            ("site_ids",        "TEXT DEFAULT ''"),
            ("mr_synced",       "INTEGER DEFAULT 0"),
            ("is_internal",     "INTEGER DEFAULT 0"),
            ("internal_note",   "TEXT DEFAULT ''"),
            ("hours_estimate",  "REAL DEFAULT 0"),
            ("checkup_rating",  "INTEGER DEFAULT 0"),
            ("planned_meeting", "DATE"),
        ]:
            try:
                conn.execute(f"ALTER TABLE clients ADD COLUMN {col} {defn}")
            except Exception:
                pass
            try:
                conn.execute(f"ALTER TABLE meetings ADD COLUMN {col} {defn}")
            except Exception:
                pass

        # Колонка менеджера для персонализации дашборда
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN manager_tg_id INTEGER DEFAULT 0")
        except Exception:
            pass

        # Таблица для хранения назначений менеджер → клиенты (персональный список)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manager_clients (
                tg_id      INTEGER NOT NULL,
                client_id  INTEGER NOT NULL REFERENCES clients(id),
                PRIMARY KEY (tg_id, client_id)
            )
        """)

        # ── Новые таблицы ──────────────────────────────────────────────────────

        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    INTEGER NOT NULL REFERENCES clients(id),
                snapshot_date DATE NOT NULL,
                health_score  INTEGER DEFAULT 0,
                color         TEXT DEFAULT 'yellow',
                notes         TEXT DEFAULT '',
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_templates (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                category      TEXT DEFAULT 'general',
                template_text TEXT NOT NULL,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                category      TEXT DEFAULT 'search',
                title         TEXT NOT NULL,
                description   TEXT DEFAULT '',
                metric_name   TEXT DEFAULT '',
                metric_result TEXT DEFAULT '',
                applies_to    TEXT DEFAULT '',
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                category   TEXT DEFAULT 'general',
                tasks_json TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_activity (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  INTEGER NOT NULL REFERENCES clients(id),
                direction  TEXT DEFAULT 'am',  -- 'am' = AM написал, 'client' = клиент написал
                note       TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS improvements (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    INTEGER NOT NULL REFERENCES clients(id),
                task_id      INTEGER REFERENCES tasks(id),
                title        TEXT NOT NULL,
                metric_name  TEXT DEFAULT '',
                metric_before TEXT DEFAULT '',
                metric_after  TEXT DEFAULT '',
                launched_at  DATE,
                result_at    DATE,
                status       TEXT DEFAULT 'running',  -- running | success | no_impact
                notes        TEXT DEFAULT '',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Миграции новых колонок ─────────────────────────────────────────────
        new_cols = [
            ("meetings",  "followup_sent",    "INTEGER DEFAULT 0"),
            ("meetings",  "followup_sent_at", "DATETIME"),
            ("meetings",  "postmit_sent",     "INTEGER DEFAULT 0"),
            ("meetings",  "postmit_sent_at",  "DATETIME"),
            ("meetings",  "qbr_score",        "INTEGER DEFAULT 0"),
            ("meetings",  "meeting_time",     "TEXT DEFAULT ''"),
            ("clients",   "health_score",     "INTEGER DEFAULT 0"),
            ("clients",   "last_chat_at",     "DATE"),
            ("clients",   "last_chat_note",   "TEXT DEFAULT ''"),
            ("tasks",     "recurring",        "INTEGER DEFAULT 0"),
            ("tasks",     "recurring_days",   "INTEGER DEFAULT 0"),
            ("tasks",     "metric_name",      "TEXT DEFAULT ''"),
            ("tasks",     "metric_before",    "TEXT DEFAULT ''"),
            ("tasks",     "metric_after",     "TEXT DEFAULT ''"),
            ("clients",   "risk_score",       "INTEGER DEFAULT 0"),
            ("clients",   "am_name",          "TEXT DEFAULT ''"),
            ("clients",   "welcome_sent",     "INTEGER DEFAULT 0"),
            ("clients",   "created_at",       "DATETIME DEFAULT CURRENT_TIMESTAMP"),
            # AR (дебиторская задолженность)
            ("clients",   "ar_amount",         "REAL DEFAULT 0"),
            ("clients",   "ar_days_overdue",   "INTEGER DEFAULT 0"),
            ("clients",   "ar_updated_at",     "DATE"),
            # Airtable
            ("clients",   "airtable_record_id","TEXT DEFAULT ''"),
            ("clients",   "contract_end_date", "DATE"),
            ("clients",   "mrr",               "REAL DEFAULT 0"),
            # MR feeds
            ("clients",   "feed_index_size",   "INTEGER DEFAULT 0"),
            ("clients",   "feed_index_limit",  "INTEGER DEFAULT 0"),
            ("clients",   "feed_status",       "TEXT DEFAULT ''"),
            ("clients",   "feed_updated_at",   "DATE"),
        ]
        for table, col, defn in new_cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            except Exception:
                pass

        # ── Новые таблицы (фаза 3+) ───────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_tags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  INTEGER NOT NULL REFERENCES clients(id),
                tag        TEXT NOT NULL,
                source     TEXT DEFAULT 'manual',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, tag)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS am_settings (
                tg_id         INTEGER NOT NULL,
                setting_key   TEXT NOT NULL,
                setting_value TEXT DEFAULT '',
                PRIMARY KEY (tg_id, setting_key)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS upsell_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL REFERENCES clients(id),
                signal_type TEXT NOT NULL,
                details     TEXT DEFAULT '',
                status      TEXT DEFAULT 'open',
                detected_at DATE NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id    INTEGER REFERENCES clients(id),
                external_id  TEXT DEFAULT '',
                subject      TEXT NOT NULL,
                status       TEXT DEFAULT 'open',
                priority     TEXT DEFAULT 'normal',
                source       TEXT DEFAULT 'time',
                days_open    INTEGER DEFAULT 0,
                url          TEXT DEFAULT '',
                task_created INTEGER DEFAULT 0,
                fetched_at   DATE NOT NULL,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(external_id)
            )
        """)

        # ── Seed шаблонов сообщений ───────────────────────────────────────────
        seed_count = conn.execute("SELECT COUNT(*) FROM message_templates").fetchone()[0]
        if seed_count == 0:
            _seed_message_templates(conn)

        # ── Seed шаблонов задач ───────────────────────────────────────────────
        tmpl_count = conn.execute("SELECT COUNT(*) FROM task_templates").fetchone()[0]
        if tmpl_count == 0:
            _seed_task_templates(conn)


# ── Clients ──────────────────────────────────────────────────────────────────

CHECKUP_DAYS = {"ENT": 30, "SME": 60, "SME+": 60, "SME-": 60, "SMB": 90, "SS": 90}


def checkup_status(last_checkup: str | None, segment: str) -> dict:
    """Статус чекапа: days_left, color (red/yellow/green), next_date, label."""
    days = CHECKUP_DAYS.get(segment, 90)
    if not last_checkup:
        return {"days_left": None, "color": "red", "next_date": None, "label": "Нет данных"}
    last = date.fromisoformat(last_checkup)
    next_date = last + timedelta(days=days)
    today = date.today()
    diff = (next_date - today).days
    if diff < 0:
        color, label = "red", f"Просрочен {abs(diff)} дн."
    elif diff <= 7:
        color, label = "yellow", f"Через {diff} дн."
    else:
        color, label = "green", f"Через {diff} дн."
    return {"days_left": diff, "color": color, "next_date": next_date.isoformat(), "label": label}


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


VALID_SEGMENTS = {"ENT", "SME+", "SME", "SME-", "SMB", "SS"}
SEGMENT_ALIASES = {
    "enterprise": "ENT", "корпоративный": "ENT", "крупный": "ENT",
    "sme plus": "SME+", "средний+": "SME+",
    "средний": "SME",
    "small medium": "SMB", "малый": "SMB", "малый бизнес": "SMB",
    "self service": "SS", "self-service": "SS", "самообслуживание": "SS",
}


def normalize_segment(raw: str) -> str:
    """Приводит произвольное название сегмента к стандартному."""
    if not raw:
        return "SME"
    r = raw.strip()
    if r in VALID_SEGMENTS:
        return r
    lo = r.lower()
    if lo in SEGMENT_ALIASES:
        return SEGMENT_ALIASES[lo]
    for alias, seg in SEGMENT_ALIASES.items():
        if alias in lo:
            return seg
    for seg in VALID_SEGMENTS:
        if seg.lower() in lo:
            return seg
    return "SME"


def upsert_client_from_airtable(
    name: str,
    segment: str,
    site_ids: str = "",
    tg_chat_id: str = "",
    am_name: str = "",
    ar_amount: float = 0.0,
    ar_days_overdue: int = 0,
    contract_end_date: str = "",
    mrr: float = 0.0,
    airtable_record_id: str = "",
) -> int:
    """
    Создаёт или обновляет клиента из данных Airtable.
    Не перезаписывает site_ids/notes/tg_chat_id если они уже есть в БД и Airtable вернул пустое.
    """
    seg = normalize_segment(segment)
    today = date.today().isoformat()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO clients
                (name, segment, site_ids, tg_chat_id, am_name,
                 ar_amount, ar_days_overdue, ar_updated_at,
                 contract_end_date, mrr, airtable_record_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                segment     = excluded.segment,
                site_ids    = CASE WHEN excluded.site_ids != ''
                                   THEN excluded.site_ids ELSE clients.site_ids END,
                tg_chat_id  = CASE WHEN excluded.tg_chat_id != ''
                                   THEN excluded.tg_chat_id ELSE clients.tg_chat_id END,
                am_name     = CASE WHEN excluded.am_name != ''
                                   THEN excluded.am_name ELSE clients.am_name END,
                ar_amount       = excluded.ar_amount,
                ar_days_overdue = excluded.ar_days_overdue,
                ar_updated_at   = excluded.ar_updated_at,
                contract_end_date = CASE WHEN excluded.contract_end_date != ''
                                         THEN excluded.contract_end_date
                                         ELSE clients.contract_end_date END,
                mrr             = CASE WHEN excluded.mrr > 0
                                       THEN excluded.mrr ELSE clients.mrr END,
                airtable_record_id = excluded.airtable_record_id
            RETURNING id
        """, (name, seg, site_ids, tg_chat_id, am_name,
              ar_amount, ar_days_overdue, today,
              contract_end_date, mrr, airtable_record_id))
        return cur.fetchone()[0]


def update_client_feeds(client_id: int, index_size: int, index_limit: int, status: str):
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE clients SET feed_index_size=?, feed_index_limit=?, feed_status=?, feed_updated_at=? WHERE id=?",
            (index_size, index_limit, status, today, client_id)
        )


def get_clients_near_index_limit(threshold: float = 0.85) -> list[dict]:
    """Клиенты у которых индекс заполнен более чем на threshold."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE feed_index_limit > 0 AND feed_index_size * 1.0 / feed_index_limit >= ?",
            (threshold,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_clients_with_contract_expiring(days_ahead: int = 60) -> list[dict]:
    """Клиенты с истекающим контрактом в ближайшие N дней."""
    today = date.today()
    deadline = (today + timedelta(days=days_ahead)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE contract_end_date IS NOT NULL AND contract_end_date BETWEEN ? AND ? ORDER BY contract_end_date",
            (today.isoformat(), deadline)
        ).fetchall()
    return [dict(r) for r in rows]


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


def set_planned_meeting(client_id: int, planned_date: str):
    """Сохранить запланированную дату следующей встречи."""
    with get_conn() as conn:
        conn.execute("UPDATE clients SET planned_meeting=? WHERE id=?", (planned_date, client_id))


def set_checkup_rating(meeting_id: int, rating: int):
    """Сохранить оценку встречи (1-5)."""
    with get_conn() as conn:
        conn.execute("UPDATE meetings SET checkup_rating=? WHERE id=?", (rating, meeting_id))


def get_upcoming_meetings(days_ahead: int = 14) -> list[dict]:
    """Встречи запланированные в ближайшие N дней."""
    from datetime import date, timedelta
    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    today  = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name, c.segment, c.planned_meeting, c.site_ids
               FROM clients c
               WHERE c.planned_meeting IS NOT NULL
                 AND c.planned_meeting >= ? AND c.planned_meeting <= ?
               ORDER BY c.planned_meeting""",
            (today, cutoff)
        ).fetchall()
    return [dict(r) for r in rows]


def get_qbr_calendar() -> list[dict]:
    """Все QBR-встречи: прошедшие + запланированные."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.*, c.name as client_name, c.segment, c.site_ids
               FROM meetings m JOIN clients c ON c.id = m.client_id
               WHERE m.meeting_type = 'qbr'
               ORDER BY m.meeting_date DESC""",
        ).fetchall()
    return [dict(r) for r in rows]


def create_internal_task(client_id: int, text: str, due_date: str = None,
                          internal_note: str = "") -> int:
    """Внутренняя задача — только в AM Hub, не синхронизируется с Merchrules."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (client_id, owner, text, due_date, is_internal, internal_note)
               VALUES (?,?,?,?,1,?) RETURNING id""",
            (client_id, "anyquery", text, due_date or None, internal_note)
        )
        return cur.fetchone()[0]


def get_internal_tasks(status: str = "open") -> list[dict]:
    """Все внутренние задачи."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.*, c.name as client_name, c.segment
               FROM tasks t JOIN clients c ON c.id = t.client_id
               WHERE t.is_internal = 1 AND t.status = ?
               ORDER BY COALESCE(t.due_date, '9999-12-31'), c.name""",
            (status,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Manager персонализация ─────────────────────────────────────────────────────

def get_manager_client_ids(tg_id: int) -> list[int]:
    """Список client_id, привязанных к менеджеру."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT client_id FROM manager_clients WHERE tg_id=?", (tg_id,)
        ).fetchall()
    return [r["client_id"] for r in rows]


def set_manager_clients(tg_id: int, client_ids: list[int]):
    """Заменяет список клиентов менеджера целиком."""
    with get_conn() as conn:
        conn.execute("DELETE FROM manager_clients WHERE tg_id=?", (tg_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO manager_clients (tg_id, client_id) VALUES (?,?)",
            [(tg_id, cid) for cid in client_ids],
        )


def get_all_clients_for_manager(tg_id: int | None = None) -> list[dict]:
    """
    Если tg_id передан и у него есть свой список — возвращает только его клиентов.
    Иначе — всех.
    """
    if tg_id:
        ids = get_manager_client_ids(tg_id)
        if ids:
            placeholders = ",".join("?" * len(ids))
            with get_conn() as conn:
                rows = conn.execute(f"""
                    SELECT c.*,
                           MAX(m.meeting_date) as last_meeting,
                           COUNT(t.id) as open_tasks
                    FROM clients c
                    LEFT JOIN meetings m ON m.client_id = c.id
                    LEFT JOIN tasks t ON t.client_id = c.id AND t.status = 'open'
                    WHERE c.id IN ({placeholders})
                    GROUP BY c.id
                    ORDER BY CASE c.segment WHEN 'ENT' THEN 1 WHEN 'SME+' THEN 2
                             WHEN 'SME' THEN 3 WHEN 'SME-' THEN 4
                             WHEN 'SMB' THEN 5 ELSE 6 END, c.name
                """, ids).fetchall()
            return [dict(r) for r in rows]
    return get_all_clients()


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


# ── Checklist templates ───────────────────────────────────────────────────────

CHECKLIST_TEMPLATES = {
    "checkup": [
        ("Как прошёл предыдущий период?", "Попросить оценить по шкале 1-10, уточнить что порадовало"),
        ("Закрытые задачи с прошлой встречи", "Пройтись по списку ниже — что реально закрыто?"),
        ("Блокеры по открытым задачам", "Есть ли задачи которые застряли? Почему?"),
        ("Новые потребности и хотелки", "Что мешает росту прямо сейчас? Что хотят добавить?"),
        ("Результаты/метрики за период", "Конверсия, GMV, CTR поиска — есть ли динамика?"),
        ("Договориться о следующей встрече", "Предложить дату через N дней согласно сегменту"),
    ],
    "qbr": [
        ("Итоги квартала — ключевые цифры", "GMV, конверсия, задачи выполнено/в работе"),
        ("Что получилось хорошо", "2-3 конкретных успеха, желательно с цифрами"),
        ("Что не получилось / что тормозило", "Честный разбор блокеров"),
        ("Приоритеты на следующий квартал", "3-5 главных задач, договориться об ответственных"),
        ("Roadmap — что запланировано от AnyQuery", "Показать что мы несём клиенту"),
        ("Риски и как закрываем", "Что может пойти не так? Чей риск?"),
        ("NPS / отношение к продукту", "Насколько довольны? Что бы изменили?"),
    ],
    "urgent": [
        ("Описать проблему точно", "Что именно сломалось? С какого момента?"),
        ("Оценить срочность (P1/P2/P3)", "P1 = стоп-бизнес, P2 = сильно мешает, P3 = неудобно"),
        ("Кто ответственный с нашей стороны", "Назначить команду/человека прямо на встрече"),
        ("Дедлайн — когда должно быть готово", "Договориться на конкретную дату"),
        ("Следующее обновление — когда", "Когда мы даём апдейт партнёру"),
    ],
    "onboarding": [
        ("Познакомиться с командой партнёра", "Узнать кто принимает решения, кто технический"),
        ("Рассказать про процессы AnyQuery", "Как работаем, как создаём задачи, как общаемся"),
        ("Доступ к дашборду — проверить", "Зайти в ЛК вместе, убедиться что всё ок"),
        ("Согласовать первые 3 задачи", "Что делаем в первые 2 недели?"),
        ("Договориться о ритме встреч", "Как часто, в каком формате, кто участвует"),
        ("Настроить TG-канал для коммуникации", "Пригласить в чат, объяснить формат"),
    ],
}


def get_checklist(client_id: int) -> list[dict]:
    """Получить текущий чеклист клиента (не закрытые + последние закрытые)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM checklist_items
               WHERE client_id = ?
               ORDER BY checked ASC, sort_order ASC, id ASC""",
            (client_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def init_checklist(client_id: int, meeting_type: str, open_tasks: list[dict],
                   meeting_id: int = None) -> list[dict]:
    """
    Создать чеклист для встречи:
    - Удаляем старый незакрытый чеклист
    - Добавляем шаблонные пункты
    - Добавляем открытые задачи клиента
    """
    templates = CHECKLIST_TEMPLATES.get(meeting_type, CHECKLIST_TEMPLATES["checkup"])

    with get_conn() as conn:
        # Удаляем незакрытые пункты предыдущего чеклиста
        conn.execute(
            "DELETE FROM checklist_items WHERE client_id = ? AND checked = 0",
            (client_id,)
        )

        # Добавляем шаблонные пункты
        for i, (text, hint) in enumerate(templates):
            conn.execute(
                """INSERT INTO checklist_items
                   (client_id, meeting_id, item_type, text, hint, sort_order)
                   VALUES (?,?,?,?,?,?)""",
                (client_id, meeting_id, "template", text, hint, i)
            )

        # Добавляем открытые задачи
        for i, task in enumerate(open_tasks):
            conn.execute(
                """INSERT INTO checklist_items
                   (client_id, meeting_id, item_type, text, task_id, sort_order)
                   VALUES (?,?,?,?,?,?)""",
                (client_id, meeting_id, "task",
                 f"[Задача] {task['text']}", task.get("id"), 100 + i)
            )

    return get_checklist(client_id)


def toggle_checklist_item(item_id: int, checked: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklist_items SET checked = ? WHERE id = ?",
            (1 if checked else 0, item_id)
        )


def add_checklist_item(client_id: int, text: str, meeting_id: int = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO checklist_items (client_id, meeting_id, item_type, text, sort_order)
               VALUES (?,?,?,?,?)""",
            (client_id, meeting_id, "custom", text, 999)
        )


def clear_checklist(client_id: int):
    """Удалить все выполненные пункты чеклиста."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM checklist_items WHERE client_id = ? AND checked = 1",
            (client_id,)
        )


def seed_clients():
    """Заполнить/обновить БД клиентами из актуального списка."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    # Всегда обновляем site_ids даже если клиенты уже есть
    for name, segment, site_ids in SEED_CLIENTS:
        upsert_client(name, segment, site_ids=site_ids)


# ── Followup ─────────────────────────────────────────────────────────────────

def get_followup_pending(days_back: int = 14) -> list[dict]:
    """Встречи за последние N дней без отправленного фолоуапа."""
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.*, c.name as client_name, c.segment, c.tg_chat_id
            FROM meetings m
            JOIN clients c ON c.id = m.client_id
            WHERE m.meeting_date >= ?
              AND m.followup_sent = 0
            ORDER BY m.meeting_date DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_all_followups(days_back: int = 30) -> list[dict]:
    """Все встречи за последние N дней с флагом фолоуапа."""
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.*, c.name as client_name, c.segment, c.tg_chat_id
            FROM meetings m
            JOIN clients c ON c.id = m.client_id
            WHERE m.meeting_date >= ?
            ORDER BY m.meeting_date DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def mark_followup_sent(meeting_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE meetings SET followup_sent=1, followup_sent_at=? WHERE id=?",
            (datetime.now().isoformat(), meeting_id)
        )


def mark_postmit_sent(meeting_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE meetings SET postmit_sent=1, postmit_sent_at=? WHERE id=?",
            (datetime.now().isoformat(), meeting_id)
        )


def set_qbr_score(meeting_id: int, score: int):
    with get_conn() as conn:
        conn.execute("UPDATE meetings SET qbr_score=? WHERE id=?", (score, meeting_id))


def set_meeting_time(meeting_id: int, meeting_time: str):
    """Установить время встречи (HH:MM)."""
    with get_conn() as conn:
        conn.execute("UPDATE meetings SET meeting_time=? WHERE id=?", (meeting_time, meeting_id))


# ── Health Score ──────────────────────────────────────────────────────────────

def calculate_health_score(client_id: int) -> dict:
    """
    Вычисляет Health Score клиента (0–100).
    Критерии:
      - Частота встреч по нормативу сегмента (25 баллов)
      - Просроченные задачи (25 баллов)
      - Mood последних 3 встреч (25 баллов)
      - Чекапы в срок (25 баллов)
    """
    client = get_client(client_id)
    if not client:
        return {"score": 0, "color": "red"}

    score = 0
    segment = client.get("segment", "SMB")
    days_norm = CHECKUP_DAYS.get(segment, 90)

    # 1. Частота встреч (25 баллов)
    last_checkup = client.get("last_checkup") or client.get("last_meeting")
    if last_checkup:
        last_date = date.fromisoformat(last_checkup)
        days_since = (date.today() - last_date).days
        ratio = days_since / days_norm
        if ratio <= 0.8:
            score += 25
        elif ratio <= 1.0:
            score += 15
        elif ratio <= 1.5:
            score += 5
        # > 1.5 нормы → 0 баллов

    # 2. Просроченные задачи (25 баллов)
    open_tasks = get_client_tasks(client_id, "open")
    today_str = date.today().isoformat()
    overdue = [t for t in open_tasks if t.get("due_date") and t["due_date"] < today_str]
    blocked = [t for t in open_tasks if t.get("status") == "blocked"]
    deductions = len(overdue) * 5 + len(blocked) * 3
    score += max(0, 25 - deductions)

    # 3. Mood последних 3 встреч (25 баллов)
    meetings = get_client_meetings(client_id, limit=3)
    if meetings:
        mood_scores = {"positive": 25, "neutral": 15, "risk": 0}
        avg_mood = sum(mood_scores.get(m.get("mood", "neutral"), 15) for m in meetings) / len(meetings)
        score += avg_mood
    else:
        score += 10  # нейтральный если нет встреч

    # 4. Чеклист/подготовленность (25 баллов) — если > 2 встреч в историии
    total_meetings = get_client_meetings(client_id, limit=20)
    if len(total_meetings) >= 3:
        score += 25
    elif len(total_meetings) >= 1:
        score += 15

    score = min(100, max(0, int(score)))
    if score >= 70:
        color = "green"
    elif score >= 40:
        color = "yellow"
    else:
        color = "red"

    return {"score": score, "color": color}


def update_client_health_score(client_id: int) -> dict:
    """Пересчитывает и сохраняет health score клиента."""
    result = calculate_health_score(client_id)
    with get_conn() as conn:
        conn.execute(
            "UPDATE clients SET health_score=? WHERE id=?",
            (result["score"], client_id)
        )
    return result


def save_health_snapshot(client_id: int, score: int, color: str, notes: str = ""):
    """Сохранить снапшот health score для трекинга тренда."""
    today = date.today().isoformat()
    with get_conn() as conn:
        # Обновляем если снапшот за сегодня уже есть
        existing = conn.execute(
            "SELECT id FROM health_history WHERE client_id=? AND snapshot_date=?",
            (client_id, today)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE health_history SET health_score=?, color=?, notes=? WHERE id=?",
                (score, color, notes, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO health_history (client_id, snapshot_date, health_score, color, notes) VALUES (?,?,?,?,?)",
                (client_id, today, score, color, notes)
            )


def get_health_history(client_id: int, months: int = 6) -> list[dict]:
    """История health score клиента за N месяцев."""
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM health_history WHERE client_id=? AND snapshot_date>=? ORDER BY snapshot_date ASC",
            (client_id, cutoff)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Message Templates ─────────────────────────────────────────────────────────

def _seed_message_templates(conn):
    """Заполнить стартовые шаблоны сообщений."""
    templates = [
        ("Статус задачи", "status",
         "Привет! Обновляем статус по задаче «{task}»: сейчас в работе у команды {team}, ожидаем готовность до {date}. Если появятся вопросы — напишите!"),
        ("Задача выполнена", "done",
         "Готово! Задача «{task}» выполнена. {result} Если хотите что-то скорректировать — пишите, разберём."),
        ("Нужна информация", "request",
         "Привет! Для продолжения работы нам нужна информация от вас: {what}. Подскажите, до {date} получим?"),
        ("Приглашение на встречу", "meeting",
         "Привет! Предлагаю встретиться {date} в {time} — обсудим {topic}. Удобно? Если нет — предложите другое время."),
        ("Фолоуап после встречи", "followup",
         "Привет! Спасибо за встречу {date}. Фиксируем договорённости:\n\n✅ AnyQuery берёт:\n{aq_tasks}\n\n📋 От вас нужно:\n{cl_tasks}\n\nСледующая встреча: {next_meeting}"),
        ("Ответ на проблему", "issue",
         "Понял, разбираемся! Передал задачу команде {team}. Дадим апдейт до {date}. Если срочно — пишите сразу."),
        ("Предложение улучшения", "improvement",
         "Привет! Подготовили предложение для {client}: {improvement}. По нашим данным у похожих клиентов это дало {result}. Когда можем обсудить?"),
    ]
    conn.executemany(
        "INSERT INTO message_templates (name, category, template_text) VALUES (?,?,?)",
        templates
    )


def get_message_templates(category: str = None) -> list[dict]:
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM message_templates WHERE category=? ORDER BY name",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM message_templates ORDER BY category, name").fetchall()
    return [dict(r) for r in rows]


def add_message_template(name: str, category: str, template_text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO message_templates (name, category, template_text) VALUES (?,?,?) RETURNING id",
            (name, category, template_text)
        )
        return cur.fetchone()[0]


def delete_message_template(template_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM message_templates WHERE id=?", (template_id,))


# ── Task Templates ────────────────────────────────────────────────────────────

def _seed_task_templates(conn):
    """Заполнить стартовые шаблоны наборов задач."""
    templates = [
        ("Онбординг ENT", "onboarding", json.dumps([
            {"text": "Познакомиться с командой клиента: кто принимает решения, кто технический", "owner": "anyquery", "days": 3},
            {"text": "Настроить TG-канал для коммуникации, объяснить формат", "owner": "anyquery", "days": 3},
            {"text": "Провести аудит текущих настроек поиска: нулевые результаты, стоп-слова, синонимы", "owner": "anyquery", "days": 7},
            {"text": "Настроить синонимы для топ-категорий по запросам", "owner": "anyquery", "days": 14},
            {"text": "Настроить стоп-слова на основе аудита", "owner": "anyquery", "days": 14},
            {"text": "Подключить бустинг для ключевых категорий/брендов", "owner": "anyquery", "days": 21},
            {"text": "Провести первый чекап: замерить метрики после настроек", "owner": "anyquery", "days": 30},
            {"text": "Предоставить доступ к личному кабинету / консоли", "owner": "client", "days": 5},
        ], ensure_ascii=False)),
        ("После QBR", "qbr", json.dumps([
            {"text": "Отправить постмит клиенту с итогами квартала", "owner": "anyquery", "days": 1},
            {"text": "Создать задачи на следующий квартал в Merchrules", "owner": "anyquery", "days": 3},
            {"text": "Обновить приоритеты roadmap по итогам обсуждения", "owner": "anyquery", "days": 3},
            {"text": "Согласовать KPI-цели на квартал с клиентом", "owner": "anyquery", "days": 7},
            {"text": "Подтвердить ответственных по каждому приоритету со стороны клиента", "owner": "client", "days": 5},
        ], ensure_ascii=False)),
        ("Аудит поиска", "audit", json.dumps([
            {"text": "Проверить нулевые результаты: скачать топ-100 запросов без результатов", "owner": "anyquery", "days": 3},
            {"text": "Проверить стоп-слова: актуальны ли все записи", "owner": "anyquery", "days": 3},
            {"text": "Проверить синонимы: покрывают ли топ-запросы", "owner": "anyquery", "days": 5},
            {"text": "Проверить бустинг: правила актуальны для текущего ассортимента", "owner": "anyquery", "days": 5},
            {"text": "Замерить конверсию поиска: текущая vs 30 дней назад", "owner": "anyquery", "days": 7},
            {"text": "Подготовить отчёт с рекомендациями", "owner": "anyquery", "days": 10},
        ], ensure_ascii=False)),
        ("Проблема с поиском", "issue", json.dumps([
            {"text": "Описать проблему точно: что именно, с какого момента, на каких запросах", "owner": "anyquery", "days": 1},
            {"text": "Диагностика: проверить индекс, настройки, логи", "owner": "anyquery", "days": 2},
            {"text": "Передать в DEV с описанием проблемы и ожидаемым поведением", "owner": "anyquery", "days": 2},
            {"text": "Дать апдейт клиенту: что нашли, когда починим", "owner": "anyquery", "days": 2},
            {"text": "Проверить результат после фикса", "owner": "anyquery", "days": 5},
        ], ensure_ascii=False)),
    ]
    conn.executemany(
        "INSERT INTO task_templates (name, category, tasks_json) VALUES (?,?,?)",
        templates
    )


def get_task_templates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM task_templates ORDER BY category, name").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["tasks"] = json.loads(d.get("tasks_json", "[]"))
        except Exception:
            d["tasks"] = []
        result.append(d)
    return result


def add_task_template(name: str, category: str, tasks: list[dict]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO task_templates (name, category, tasks_json) VALUES (?,?,?) RETURNING id",
            (name, category, json.dumps(tasks, ensure_ascii=False))
        )
        return cur.fetchone()[0]


def delete_task_template(template_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM task_templates WHERE id=?", (template_id,))


# ── Knowledge Base ────────────────────────────────────────────────────────────

def get_knowledge_base(category: str = None) -> list[dict]:
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM knowledge_base WHERE category=? ORDER BY created_at DESC",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM knowledge_base ORDER BY category, created_at DESC").fetchall()
    return [dict(r) for r in rows]


def add_knowledge_item(category: str, title: str, description: str,
                       metric_name: str = "", metric_result: str = "",
                       applies_to: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO knowledge_base (category, title, description, metric_name, metric_result, applies_to)
               VALUES (?,?,?,?,?,?) RETURNING id""",
            (category, title, description, metric_name, metric_result, applies_to)
        )
        return cur.fetchone()[0]


def delete_knowledge_item(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM knowledge_base WHERE id=?", (item_id,))


# ── Chat Activity ─────────────────────────────────────────────────────────────

CHAT_NORM_DAYS = {"ENT": 7, "SME+": 14, "SME": 14, "SME-": 14, "SMB": 30, "SS": 30}


def log_chat_activity(client_id: int, direction: str = "am", note: str = "") -> int:
    """Записать факт коммуникации с клиентом."""
    today = date.today().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_activity (client_id, direction, note) VALUES (?,?,?) RETURNING id",
            (client_id, direction, note)
        )
        record_id = cur.fetchone()[0]
        # Обновляем дату последней коммуникации
        conn.execute(
            "UPDATE clients SET last_chat_at=?, last_chat_note=? WHERE id=?",
            (today, note[:200] if note else "", client_id)
        )
    return record_id


def get_chat_activity(client_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_activity WHERE client_id=? ORDER BY created_at DESC LIMIT ?",
            (client_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_clients_without_recent_chat(days_threshold: int = None) -> list[dict]:
    """Клиенты у которых нет активности в чате дольше нормативного срока."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*, MAX(ca.created_at) as last_activity
            FROM clients c
            LEFT JOIN chat_activity ca ON ca.client_id = c.id
            GROUP BY c.id
        """).fetchall()

    result = []
    today = date.today()
    for row in rows:
        r = dict(row)
        segment = r.get("segment", "SMB")
        norm = days_threshold or CHAT_NORM_DAYS.get(segment, 30)
        last = r.get("last_activity")
        if last:
            try:
                last_date = datetime.fromisoformat(last).date()
                days_ago = (today - last_date).days
            except Exception:
                days_ago = 9999
        else:
            days_ago = 9999

        r["days_since_chat"] = days_ago
        r["chat_norm_days"] = norm
        r["chat_overdue"] = days_ago > norm
        result.append(r)

    return sorted(result, key=lambda x: -x["days_since_chat"])


# ── Improvements / A-B tracker ────────────────────────────────────────────────

def get_improvements(client_id: int = None) -> list[dict]:
    with get_conn() as conn:
        if client_id:
            rows = conn.execute(
                """SELECT i.*, c.name as client_name, c.segment
                   FROM improvements i JOIN clients c ON c.id = i.client_id
                   WHERE i.client_id=? ORDER BY i.created_at DESC""",
                (client_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT i.*, c.name as client_name, c.segment
                   FROM improvements i JOIN clients c ON c.id = i.client_id
                   ORDER BY i.created_at DESC"""
            ).fetchall()
    return [dict(r) for r in rows]


def add_improvement(client_id: int, title: str, metric_name: str = "",
                    metric_before: str = "", launched_at: str = None,
                    notes: str = "", task_id: int = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO improvements
               (client_id, task_id, title, metric_name, metric_before, launched_at, notes)
               VALUES (?,?,?,?,?,?,?) RETURNING id""",
            (client_id, task_id, title, metric_name, metric_before,
             launched_at or date.today().isoformat(), notes)
        )
        return cur.fetchone()[0]


def update_improvement_result(improvement_id: int, metric_after: str,
                               result_at: str = None, status: str = "success",
                               notes: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE improvements SET metric_after=?, result_at=?, status=?, notes=?
               WHERE id=?""",
            (metric_after, result_at or date.today().isoformat(), status, notes, improvement_id)
        )


def delete_improvement(improvement_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM improvements WHERE id=?", (improvement_id,))


# ── Recurring tasks ───────────────────────────────────────────────────────────

def get_recurring_tasks_to_create() -> list[dict]:
    """Повторяющиеся задачи которые нужно создать сегодня (все завершены и прошло recurring_days)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, c.name as client_name
            FROM tasks t JOIN clients c ON c.id = t.client_id
            WHERE t.recurring = 1 AND t.status = 'done' AND t.recurring_days > 0
        """).fetchall()

    result = []
    today = date.today()
    for row in rows:
        r = dict(row)
        # Проверяем нет ли уже активной копии
        with get_conn() as conn:
            active = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE client_id=? AND text=? AND status='open' AND recurring=1",
                (r["client_id"], r["text"])
            ).fetchone()[0]
        if active:
            continue
        # Проверяем когда была закрыта задача
        closed_date_str = r.get("due_date") or r.get("created_at", "")[:10]
        try:
            closed_date = date.fromisoformat(closed_date_str)
            if (today - closed_date).days >= r["recurring_days"]:
                result.append(r)
        except Exception:
            pass
    return result


def create_recurring_copy(task: dict) -> int:
    """Создать новую копию повторяющейся задачи."""
    days = task.get("recurring_days", 30)
    new_due = (date.today() + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (client_id, owner, text, due_date, recurring, recurring_days, is_internal)
               VALUES (?,?,?,?,1,?,?) RETURNING id""",
            (task["client_id"], task["owner"], task["text"], new_due,
             task["recurring_days"], task.get("is_internal", 0))
        )
        return cur.fetchone()[0]


# ── Manager TG IDs для персональных уведомлений ──────────────────────────────

def get_all_manager_tg_ids() -> list[int]:
    """Все TG ID менеджеров у которых есть свой список клиентов."""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT tg_id FROM manager_clients").fetchall()
    return [r["tg_id"] for r in rows]


def get_manager_info(tg_id: int) -> dict | None:
    """Получить данные менеджера по tg_id."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    return dict(row) if row else None


# ── Risk Score ────────────────────────────────────────────────────────────────

def calculate_risk_score(client_id: int) -> dict:
    """
    Вычисляет Risk Score (вероятность оттока) 0–100.
    Чем выше — тем выше риск потери клиента.
    Компоненты:
      - Частота встреч (0-30 баллов риска)
      - Mood последних встреч (0-30 баллов риска)
      - Просроченные задачи (0-25 баллов риска)
      - Отсутствие коммуникации в чате (0-15 баллов риска)
    """
    client = get_client(client_id)
    if not client:
        return {"score": 0, "level": "low", "reasons": []}

    risk = 0
    reasons = []
    segment = client.get("segment", "SMB")
    days_norm = CHECKUP_DAYS.get(segment, 90)

    # 1. Частота встреч (30 баллов)
    last_checkup = client.get("last_checkup") or client.get("last_meeting")
    if not last_checkup:
        risk += 30
        reasons.append("Встреч не проводилось")
    else:
        last_date = date.fromisoformat(last_checkup)
        days_since = (date.today() - last_date).days
        ratio = days_since / days_norm
        if ratio > 2.0:
            risk += 30
            reasons.append(f"Нет встречи {days_since} дней (норма {days_norm})")
        elif ratio > 1.5:
            risk += 20
            reasons.append(f"Встреча просрочена на {days_since - days_norm} дней")
        elif ratio > 1.0:
            risk += 10

    # 2. Mood последних встреч (30 баллов)
    meetings = get_client_meetings(client_id, limit=5)
    if not meetings:
        risk += 15
    else:
        risk_meetings = sum(1 for m in meetings if m.get("mood") == "risk")
        neutral_meetings = sum(1 for m in meetings if m.get("mood") == "neutral")
        if risk_meetings >= 2:
            risk += 30
            reasons.append(f"{risk_meetings} встречи с негативным настроением")
        elif risk_meetings == 1:
            risk += 15
            if risk_meetings:
                reasons.append("1 встреча с негативным настроением")
        elif neutral_meetings >= 3:
            risk += 8

    # 3. Просроченные задачи (25 баллов)
    open_tasks = get_client_tasks(client_id, "open")
    today_str = date.today().isoformat()
    overdue = [t for t in open_tasks if t.get("due_date") and t["due_date"] < today_str]
    blocked = [t for t in open_tasks if t.get("status") == "blocked"]
    task_risk = len(overdue) * 4 + len(blocked) * 3
    task_risk = min(25, task_risk)
    if task_risk > 0:
        risk += task_risk
        if overdue:
            reasons.append(f"{len(overdue)} просроченных задач")
        if blocked:
            reasons.append(f"{len(blocked)} заблокированных задач")

    # 4. Коммуникация в чате (15 баллов)
    chat_norm = CHAT_NORM_DAYS.get(segment, 30)
    last_chat = client.get("last_chat_at")
    if not last_chat:
        risk += 15
        reasons.append("Нет активности в чате")
    else:
        try:
            chat_date = date.fromisoformat(last_chat)
            days_chat = (date.today() - chat_date).days
            if days_chat > chat_norm * 2:
                risk += 15
                reasons.append(f"Нет чата {days_chat} дней")
            elif days_chat > chat_norm:
                risk += 7
        except Exception:
            pass

    score = min(100, max(0, risk))
    if score >= 70:
        level = "critical"
    elif score >= 40:
        level = "medium"
    else:
        level = "low"

    return {"score": score, "level": level, "reasons": reasons}


def update_client_risk_score(client_id: int) -> dict:
    """Пересчитывает и сохраняет risk score клиента."""
    result = calculate_risk_score(client_id)
    with get_conn() as conn:
        conn.execute(
            "UPDATE clients SET risk_score=? WHERE id=?",
            (result["score"], client_id)
        )
    return result


def get_clients_high_risk(threshold: int = 70) -> list[dict]:
    """Клиенты с высоким риском оттока."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, MAX(m.meeting_date) as last_meeting
               FROM clients c
               LEFT JOIN meetings m ON m.client_id = c.id
               WHERE c.risk_score >= ?
               GROUP BY c.id
               ORDER BY c.risk_score DESC""",
            (threshold,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_clients_with_meeting_today() -> list[dict]:
    """Клиенты у которых запланирована встреча на сегодня."""
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM clients
               WHERE planned_meeting = ?
               ORDER BY name""",
            (today,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_segment_health_median() -> dict:
    """Медианный health_score по каждому сегменту."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT segment, health_score FROM clients
            WHERE health_score > 0
            ORDER BY segment, health_score
        """).fetchall()

    by_segment = {}
    for row in rows:
        seg = row["segment"]
        by_segment.setdefault(seg, []).append(row["health_score"])

    result = {}
    for seg, scores in by_segment.items():
        n = len(scores)
        if n == 0:
            result[seg] = 0
        elif n % 2 == 1:
            result[seg] = scores[n // 2]
        else:
            result[seg] = (scores[n // 2 - 1] + scores[n // 2]) // 2

    return result


def log_platform_audit(client_id: int, issues: list[dict]):
    """Сохранить результаты аудита платформы."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_audits (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL REFERENCES clients(id),
                audit_date  DATE NOT NULL,
                issues_json TEXT DEFAULT '[]',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO platform_audits (client_id, audit_date, issues_json) VALUES (?,?,?)",
            (client_id, date.today().isoformat(), json.dumps(issues, ensure_ascii=False))
        )


# ── Ж3: Годовщины клиентов ────────────────────────────────────────────────────

def get_clients_with_anniversary_soon(days_ahead: int = 7) -> list[dict]:
    """
    Клиенты у которых через days_ahead дней (±) будет годовщина (1/2/3 года).
    Ориентируемся по первой встрече (min meeting_date).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT c.*, MIN(m.meeting_date) as first_meeting
            FROM clients c
            JOIN meetings m ON m.client_id = c.id
            GROUP BY c.id
            HAVING first_meeting IS NOT NULL
        """).fetchall()

    result = []
    today = date.today()
    for row in rows:
        r = dict(row)
        first = r.get("first_meeting")
        if not first:
            continue
        try:
            first_date = date.fromisoformat(first[:10])
        except Exception:
            continue

        for years in (1, 2, 3, 5):
            try:
                anniversary = first_date.replace(year=first_date.year + years)
            except ValueError:
                anniversary = first_date.replace(year=first_date.year + years, day=28)
            diff = (anniversary - today).days
            if -1 <= diff <= days_ahead:
                r["anniversary_years"] = years
                r["anniversary_date"] = anniversary.isoformat()
                r["anniversary_days_left"] = diff
                result.append(r)
                break

    return result


# ── Ж4: Welcome-sequence для новых клиентов ───────────────────────────────────

def get_new_clients_for_welcome() -> list[dict]:
    """Новые клиенты (добавлены за последние 3 дня) без отправленного welcome."""
    cutoff = (date.today() - timedelta(days=3)).isoformat()
    with get_conn() as conn:
        # Убеждаемся что колонка есть
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN welcome_sent INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        except Exception:
            pass

        rows = conn.execute("""
            SELECT * FROM clients
            WHERE welcome_sent = 0
              AND created_at >= ?
              AND tg_chat_id IS NOT NULL AND tg_chat_id != ''
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def mark_welcome_sent(client_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE clients SET welcome_sent=1 WHERE id=?", (client_id,))


# ── З3: Кросс-клиентные паттерны ─────────────────────────────────────────────

def get_common_task_patterns(days_back: int = 7, min_count: int = 3) -> list[dict]:
    """
    Находит общие паттерны в задачах за последние N дней.
    Возвращает группы похожих задач (по ключевым словам).
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.text, t.client_id, c.name as client_name, c.segment, t.created_at
            FROM tasks t JOIN clients c ON c.id = t.client_id
            WHERE t.created_at >= ?
              AND t.is_internal = 0
            ORDER BY t.created_at DESC
        """, (cutoff,)).fetchall()

    # Ключевые слова для кластеризации
    keywords = [
        "синоним", "стоп-слов", "нулевые", "конверсия", "ctr", "буст",
        "индекс", "поиск", "фильтр", "заблокировано", "ошибка", "проблема",
        "аудит", "настройк", "интеграц",
    ]

    patterns = {}
    for row in rows:
        r = dict(row)
        text_lower = r["text"].lower()
        for kw in keywords:
            if kw in text_lower:
                if kw not in patterns:
                    patterns[kw] = []
                patterns[kw].append(r)
                break

    return [
        {"keyword": kw, "count": len(tasks), "clients": tasks, "keyword_label": kw}
        for kw, tasks in patterns.items()
        if len(tasks) >= min_count
    ]


# ── З4: Авто-закрытие устаревших задач ────────────────────────────────────────

def get_open_tasks_older_than(days: int = 60) -> list[dict]:
    """Задачи открытые более N дней без изменений."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, c.name as client_name, c.segment
            FROM tasks t JOIN clients c ON c.id = t.client_id
            WHERE t.status = 'open'
              AND t.created_at < ?
              AND t.is_internal = 0
            ORDER BY t.created_at ASC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


# ── К3: Сезонные события ──────────────────────────────────────────────────────

SEASONAL_EVENTS = [
    {"name": "Новый год",     "month": 12, "day": 1,  "days_before": 21,
     "tasks": ["Настроить сезонный буст для новогодних товаров", "Добавить синонимы: подарки, новый год, ёлка", "Проверить актуальность стоп-слов перед пиковым сезоном"]},
    {"name": "8 Марта",       "month": 2,  "day": 15, "days_before": 21,
     "tasks": ["Настроить буст для товаров к 8 марта: цветы, парфюм, украшения", "Добавить синонимы: 8 марта, международный женский день, подарок маме"]},
    {"name": "23 Февраля",    "month": 2,  "day": 2,  "days_before": 14,
     "tasks": ["Настроить буст для мужских товаров к 23 февраля", "Проверить синонимы: день защитника, подарок мужчине, папе"]},
    {"name": "11.11 Распродажа", "month": 10, "day": 20, "days_before": 21,
     "tasks": ["Настроить буст топ-товаров к распродаже 11.11", "Проверить скорость поиска под пиковую нагрузку", "Добавить синонимы для акционных запросов"]},
    {"name": "Чёрная пятница", "month": 11, "day": 1, "days_before": 21,
     "tasks": ["Настроить буст для акционных товаров к Чёрной пятнице", "Проверить бустинг: скидки, акция, распродажа"]},
    {"name": "День знаний",   "month": 8,  "day": 15, "days_before": 14,
     "tasks": ["Настроить буст для школьных товаров: канцелярия, рюкзаки", "Добавить синонимы: 1 сентября, школа, учёба"]},
]


def get_upcoming_seasonal_events(days_ahead: int = 21) -> list[dict]:
    """Ближайшие сезонные события для подготовки."""
    today = date.today()
    result = []
    for event in SEASONAL_EVENTS:
        try:
            event_date = date(today.year, event["month"], event["day"])
            if event_date < today:
                event_date = date(today.year + 1, event["month"], event["day"])
            diff = (event_date - today).days
            if 0 <= diff <= days_ahead:
                result.append({**event, "date": event_date.isoformat(), "days_left": diff})
        except Exception:
            pass
    return result


# ── Клиентские теги ───────────────────────────────────────────────────────────

def get_client_tags(client_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tag FROM client_tags WHERE client_id=? ORDER BY source DESC, created_at DESC",
            (client_id,)
        ).fetchall()
    return [r["tag"] for r in rows]


def set_client_tags(client_id: int, tags: list[str], source: str = "manual"):
    """Заменяет теги клиента (из указанного источника) новым списком."""
    with get_conn() as conn:
        conn.execute("DELETE FROM client_tags WHERE client_id=? AND source=?", (client_id, source))
        for tag in tags:
            tag = tag.strip()[:50]
            if tag:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO client_tags(client_id, tag, source) VALUES(?,?,?)",
                        (client_id, tag, source)
                    )
                except Exception:
                    pass


def get_all_clients_with_tags() -> list[dict]:
    """Все клиенты + их теги (для AI авто-тегирования)."""
    clients = get_all_clients()
    for c in clients:
        c["tags"] = get_client_tags(c["id"])
    return clients


# ── AM Персональные настройки ─────────────────────────────────────────────────

AM_SETTING_DEFAULTS = {
    "morning_plan_hour":     "9",
    "health_alert_threshold": "50",
    "notify_overdue_checkups": "1",
    "notify_ai_priority":    "1",
    "notify_stale_tasks":    "1",
    "notify_upsell":         "1",
    "notify_ar_overdue":     "1",
    "notify_tickets":        "1",
    "dashboard_sort":        "health",   # health | risk | name | segment | ar
    "dashboard_filter":      "all",      # all | mine
    "show_ar_badge":         "1",
    "show_tags":             "1",
    "show_upsell_badge":     "1",
    "off_hours_start":       "21",
    "off_hours_end":         "8",
    "timezone":              "Europe/Moscow",
}


def get_am_setting(tg_id: int, key: str, default: str | None = None) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT setting_value FROM am_settings WHERE tg_id=? AND setting_key=?",
            (tg_id, key)
        ).fetchone()
    if row:
        return row["setting_value"]
    return default if default is not None else AM_SETTING_DEFAULTS.get(key, "")


def set_am_setting(tg_id: int, key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO am_settings(tg_id, setting_key, setting_value) VALUES(?,?,?)",
            (tg_id, key, str(value))
        )


def get_am_settings(tg_id: int) -> dict:
    """Все настройки AM: дефолты + переопределённые."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT setting_key, setting_value FROM am_settings WHERE tg_id=?", (tg_id,)
        ).fetchall()
    settings = dict(AM_SETTING_DEFAULTS)
    for r in rows:
        settings[r["setting_key"]] = r["setting_value"]
    return settings


def save_am_settings(tg_id: int, settings: dict):
    """Сохранить несколько настроек разом."""
    allowed = set(AM_SETTING_DEFAULTS.keys())
    with get_conn() as conn:
        for key, value in settings.items():
            if key in allowed:
                conn.execute(
                    "INSERT OR REPLACE INTO am_settings(tg_id, setting_key, setting_value) VALUES(?,?,?)",
                    (tg_id, key, str(value))
                )


# ── AR — Дебиторская задолженность ───────────────────────────────────────────

def update_client_ar(client_id: int, ar_amount: float, ar_days_overdue: int):
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE clients SET ar_amount=?, ar_days_overdue=?, ar_updated_at=? WHERE id=?",
            (ar_amount, ar_days_overdue, today, client_id)
        )


def get_clients_with_ar(min_amount: float = 1.0) -> list[dict]:
    """Клиенты с ненулевой задолженностью."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE ar_amount >= ? ORDER BY ar_days_overdue DESC",
            (min_amount,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_ar_overdue_clients(days: int = 30) -> list[dict]:
    """Клиенты с просрочкой более N дней."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients WHERE ar_days_overdue >= ? AND ar_amount > 0 ORDER BY ar_days_overdue DESC",
            (days,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Апсел-сигналы ─────────────────────────────────────────────────────────────

def save_upsell_signal(client_id: int, signal_type: str, details: str = "") -> int:
    today = date.today().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO upsell_signals(client_id, signal_type, details, detected_at) VALUES(?,?,?,?)",
            (client_id, signal_type, details, today)
        )
    return cur.lastrowid


def get_open_upsell_signals() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT u.*, c.name as client_name, c.segment
            FROM upsell_signals u JOIN clients c ON c.id = u.client_id
            WHERE u.status = 'open'
            ORDER BY u.detected_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def update_upsell_signal(signal_id: int, status: str):
    with get_conn() as conn:
        conn.execute("UPDATE upsell_signals SET status=? WHERE id=?", (status, signal_id))


def get_client_upsell_signals(client_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM upsell_signals WHERE client_id=? ORDER BY detected_at DESC LIMIT 5",
            (client_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Тикеты поддержки ──────────────────────────────────────────────────────────

def upsert_support_ticket(
    external_id: str, client_id: int | None, subject: str,
    status: str, priority: str, days_open: int, url: str = "", source: str = "time",
) -> int:
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO support_tickets
                (external_id, client_id, subject, status, priority, days_open, url, source, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(external_id) DO UPDATE SET
                status=excluded.status, days_open=excluded.days_open, fetched_at=excluded.fetched_at
        """, (external_id, client_id, subject, status, priority, days_open, url, source, today))
        row = conn.execute(
            "SELECT id FROM support_tickets WHERE external_id=?", (external_id,)
        ).fetchone()
    return row["id"] if row else 0


def get_old_open_tickets(days: int = 3) -> list[dict]:
    """Тикеты открытые более N дней без задачи AM."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, c.name as client_name, c.segment, c.am_name
            FROM support_tickets t
            LEFT JOIN clients c ON c.id = t.client_id
            WHERE t.status NOT IN ('resolved','closed')
              AND t.days_open >= ?
              AND t.task_created = 0
            ORDER BY t.days_open DESC
        """, (days,)).fetchall()
    return [dict(r) for r in rows]


def mark_ticket_task_created(ticket_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE support_tickets SET task_created=1 WHERE id=?", (ticket_id,))
