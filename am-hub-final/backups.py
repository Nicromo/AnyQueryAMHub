"""Per-manager backups — gzip JSON-снапшоты данных одного менеджера."""
import gzip, json, os, re, logging
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import inspect

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(BASE_DIR / "_backups")))


def _safe_email(email: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", (email or "unknown").lower())


def _row_to_dict(obj) -> dict:
    m = inspect(obj.__class__).mapper.columns
    out = {}
    for col in m:
        v = getattr(obj, col.key, None)
        if isinstance(v, datetime):
            out[col.key] = v.isoformat()
        else:
            out[col.key] = v
    return out


def backup_manager(db: Session, manager_email: str, out_dir: Path = BACKUP_DIR) -> Path:
    """Создать gzip-JSON бэкап для одного менеджера."""
    from models import (
        Client, Task, Meeting, CheckUp, ClientNote, PartnerLog,
        QBR, ClientContact, ClientProduct, ClientMerchRule, ClientFeed,
        VoiceNote, SupportTicket, TicketComment, AutoTaskRule,
        RevenueEntry,
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    client_ids = [c.id for c in db.query(Client.id).filter(Client.manager_email == manager_email).all()]

    data = {
        "manager_email": manager_email,
        "created_at":    datetime.utcnow().isoformat(),
        "clients":       [_row_to_dict(c) for c in db.query(Client).filter(Client.manager_email == manager_email).all()],
    }
    if client_ids:
        for Model, key in [
            (Task, "tasks"), (Meeting, "meetings"), (CheckUp, "checkups"),
            (ClientNote, "notes"), (PartnerLog, "partner_logs"), (QBR, "qbrs"),
            (ClientContact, "contacts"), (ClientProduct, "products"),
            (ClientMerchRule, "merch_rules"), (ClientFeed, "feeds"),
            (VoiceNote, "voice_notes"), (SupportTicket, "tickets"),
            (RevenueEntry, "revenue_entries"),
        ]:
            try:
                rows = db.query(Model).filter(Model.client_id.in_(client_ids)).all()
                data[key] = [_row_to_dict(r) for r in rows]
            except Exception as e:
                logger.warning("backup skip %s: %s", key, e)
                data[key] = []
    try:
        data["auto_task_rules"] = [_row_to_dict(r) for r in db.query(AutoTaskRule).filter(AutoTaskRule.manager_email == manager_email).all()]
    except Exception:
        data["auto_task_rules"] = []
    try:
        if data.get("tickets"):
            tids = [t["id"] for t in data["tickets"]]
            data["ticket_comments"] = [_row_to_dict(r) for r in db.query(TicketComment).filter(TicketComment.ticket_id.in_(tids)).all()]
        else:
            data["ticket_comments"] = []
    except Exception:
        data["ticket_comments"] = []

    stamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    path = out_dir / f"{_safe_email(manager_email)}__{stamp}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    return path


def backup_all_managers(db: Session, out_dir: Path = BACKUP_DIR) -> list[Path]:
    from models import Client, User
    emails = [e for (e,) in db.query(User.email).filter(User.is_active == True, User.email.isnot(None)).all() if e]
    results = []
    for email in emails:
        try:
            if db.query(Client).filter(Client.manager_email == email).count() > 0:
                results.append(backup_manager(db, email, out_dir))
        except Exception as e:
            logger.exception("backup failed for %s: %s", email, e)
    return results


def list_backups(out_dir: Path = BACKUP_DIR) -> list[dict]:
    if not out_dir.exists():
        return []
    items = []
    for p in sorted(out_dir.glob("*.json.gz"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        items.append({
            "filename": p.name,
            "size":     st.st_size,
            "mtime":    datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    return items


def cleanup_old_backups(out_dir: Path = BACKUP_DIR, keep_days: int = 30) -> int:
    if not out_dir.exists():
        return 0
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    removed = 0
    for p in out_dir.glob("*.json.gz"):
        mt = datetime.fromtimestamp(p.stat().st_mtime)
        if mt < cutoff:
            try:
                p.unlink(); removed += 1
            except Exception as e:
                logger.warning(f"cleanup_old_backups: cannot unlink {p}: {e}")
    return removed
