"""
Google Sheets sync — Churn/Downsell и Top-50.
Требует GOOGLE_SHEETS_CREDS (JSON строка service account) или
GOOGLE_SHEETS_API_KEY (только для чтения публичных листов).

Листы:
  Churn/Downsell: 1Tkax6awhWmNXfXpzORPIqHy5qgAhLzfifSHc-YLQhhY
  Top-50:         10SuYn0w2VyDU87KSrYE-A_TDqkekj7q__o910doRCsc
"""
import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SHEETS_API_KEY    = os.getenv("GOOGLE_SHEETS_API_KEY", "")
SHEETS_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS", "")  # service account JSON string

CHURN_SHEET_ID = "1Tkax6awhWmNXfXpzORPIqHy5qgAhLzfifSHc-YLQhhY"
TOP50_SHEET_ID = "10SuYn0w2VyDU87KSrYE-A_TDqkekj7q__o910doRCsc"


async def fetch_sheet_range(sheet_id: str, range_: str = "A1:Z1000", api_key: str = "") -> list:
    """Fetch values from Google Sheets via REST API (requires API key or service account)."""
    key = api_key or SHEETS_API_KEY
    if not key:
        logger.warning("No Google Sheets API key configured")
        return []
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_}"
    async with httpx.AsyncClient(timeout=20) as hx:
        try:
            resp = await hx.get(url, params={"key": key})
            if resp.status_code != 200:
                logger.error("Sheets error %d: %s", resp.status_code, resp.text[:200])
                return []
            return resp.json().get("values", [])
        except Exception as e:
            logger.error("fetch_sheet_range: %s", e)
            return []


def _parse_float(v) -> Optional[float]:
    """Parse a cell value to float, handling various formats."""
    if v is None:
        return None
    s = str(v).replace("\u00a0", "").replace(" ", "").replace(",", ".").replace("%", "").strip()
    if not s or s == "—" or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


async def sync_churn_sheet(db) -> dict:
    """Import churn/downsell data from Google Sheets → UpsellEvent table.

    Sheet columns expected (header in row 1):
      Клиент | Тип (churn_risk/downsell/upsell) | MRR до | MRR после | Статус | Дата | Комментарий
    """
    from models import UpsellEvent, Client
    from datetime import datetime

    if not SHEETS_API_KEY and not SHEETS_CREDS_JSON:
        return {"ok": False, "error": "GOOGLE_SHEETS_API_KEY not configured", "created": 0, "updated": 0}

    rows = await fetch_sheet_range(CHURN_SHEET_ID, "A1:H500")
    if not rows or len(rows) < 2:
        return {"ok": False, "error": "Empty sheet or access denied", "created": 0, "updated": 0}

    # Build header map from first row
    header = [str(h).strip().lower() for h in rows[0]]

    def _col(name_candidates: list) -> Optional[int]:
        for cand in name_candidates:
            for i, h in enumerate(header):
                if cand in h:
                    return i
        return None

    ci_client   = _col(["клиент", "client", "account", "название"])
    ci_type     = _col(["тип", "type", "event"])
    ci_before   = _col(["до", "before", "mrr до", "mrr_before"])
    ci_after    = _col(["после", "after", "mrr после", "mrr_after"])
    ci_status   = _col(["статус", "status"])
    ci_date     = _col(["дата", "date"])
    ci_comment  = _col(["комментарий", "comment", "описание", "description"])

    if ci_client is None:
        return {"ok": False, "error": "Column 'Клиент' not found in sheet", "created": 0, "updated": 0}

    def _cell(row: list, idx: Optional[int]) -> str:
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    created = updated = skipped = 0
    errors = []

    for raw_row in rows[1:]:
        if not raw_row or not any(raw_row):
            continue
        client_name = _cell(raw_row, ci_client)
        if not client_name:
            skipped += 1
            continue

        event_type  = _cell(raw_row, ci_type) or "churn_risk"
        # Normalize event type
        et_lower = event_type.lower()
        if "churn" in et_lower or "отток" in et_lower:
            event_type = "churn_risk"
        elif "down" in et_lower or "даун" in et_lower:
            event_type = "downsell"
        elif "up" in et_lower or "апсейл" in et_lower:
            event_type = "upsell"
        else:
            event_type = "churn_risk"

        amount_before = _parse_float(_cell(raw_row, ci_before))
        amount_after  = _parse_float(_cell(raw_row, ci_after))
        delta = None
        if amount_before is not None and amount_after is not None:
            delta = amount_after - amount_before

        status_raw = _cell(raw_row, ci_status) or "identified"
        # Normalize status
        st_lower = status_raw.lower()
        if "won" in st_lower or "выигра" in st_lower or "продлил" in st_lower:
            status = "won"
        elif "lost" in st_lower or "проигра" in st_lower or "ушёл" in st_lower or "ушел" in st_lower:
            status = "lost"
        elif "progr" in st_lower or "работ" in st_lower or "in_progress" in st_lower:
            status = "in_progress"
        elif "post" in st_lower or "откл" in st_lower:
            status = "postponed"
        else:
            status = "identified"

        description = _cell(raw_row, ci_comment)

        date_raw = _cell(raw_row, ci_date)
        due_date = None
        if date_raw:
            from datetime import datetime as _dt
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    due_date = _dt.strptime(date_raw[:10], fmt)
                    break
                except ValueError:
                    continue

        # Find client
        client = db.query(Client).filter(Client.name == client_name).first()
        if not client:
            # Fuzzy match: try contains
            client = db.query(Client).filter(Client.name.ilike(f"%{client_name[:20]}%")).first()

        try:
            # Check for existing event matching client+type (simple dedup)
            existing = None
            if client:
                existing = db.query(UpsellEvent).filter(
                    UpsellEvent.client_id == client.id,
                    UpsellEvent.event_type == event_type,
                    UpsellEvent.status == status,
                ).first()

            if existing:
                if amount_before is not None:
                    existing.amount_before = amount_before
                if amount_after is not None:
                    existing.amount_after = amount_after
                if delta is not None:
                    existing.delta = delta
                if description:
                    existing.description = description
                if due_date:
                    existing.due_date = due_date
                updated += 1
            else:
                ev = UpsellEvent(
                    client_id=client.id if client else None,
                    event_type=event_type,
                    status=status,
                    amount_before=amount_before,
                    amount_after=amount_after,
                    delta=delta,
                    description=description or client_name,
                    due_date=due_date,
                    created_by="sheets_sync",
                )
                db.add(ev)
                created += 1
        except Exception as exc:
            errors.append(f"{client_name}: {exc}")
            skipped += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}", "created": created, "updated": updated, "errors": errors[:5]}

    return {"ok": True, "rows": len(rows) - 1, "created": created, "updated": updated,
            "skipped": skipped, "errors": errors[:5]}


async def sync_top50_sheet(db) -> dict:
    """Import Top-50 data from Google Sheets → update Client.gmv etc.

    Sheet columns expected (header in row 1):
      Клиент | Сегмент | GMV | MRR | Менеджер | Домен | Статус
    """
    from models import Client
    from datetime import datetime

    if not SHEETS_API_KEY and not SHEETS_CREDS_JSON:
        return {"ok": False, "error": "GOOGLE_SHEETS_API_KEY not configured", "updated": 0}

    rows = await fetch_sheet_range(TOP50_SHEET_ID, "A1:J200")
    if not rows or len(rows) < 2:
        return {"ok": False, "error": "Empty sheet or access denied", "updated": 0}

    header = [str(h).strip().lower() for h in rows[0]]

    def _col(name_candidates: list) -> Optional[int]:
        for cand in name_candidates:
            for i, h in enumerate(header):
                if cand in h:
                    return i
        return None

    ci_client  = _col(["клиент", "client", "account", "название", "name"])
    ci_segment = _col(["сегмент", "segment", "stage"])
    ci_gmv     = _col(["gmv", "оборот", "revenue"])
    ci_mrr     = _col(["mrr", "мрр"])
    ci_manager = _col(["менеджер", "manager", "csm", "am "])
    ci_domain  = _col(["домен", "domain", "url", "сайт"])

    if ci_client is None:
        return {"ok": False, "error": "Column 'Клиент' not found in sheet", "updated": 0}

    def _cell(row: list, idx: Optional[int]) -> str:
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    updated = skipped = 0
    errors = []

    for raw_row in rows[1:]:
        if not raw_row or not any(raw_row):
            continue
        client_name = _cell(raw_row, ci_client)
        if not client_name:
            skipped += 1
            continue

        gmv = _parse_float(_cell(raw_row, ci_gmv))
        mrr = _parse_float(_cell(raw_row, ci_mrr))
        segment_raw = _cell(raw_row, ci_segment)
        manager_raw = _cell(raw_row, ci_manager)
        domain_raw  = _cell(raw_row, ci_domain)

        # Find client
        client = db.query(Client).filter(Client.name == client_name).first()
        if not client:
            client = db.query(Client).filter(Client.name.ilike(f"%{client_name[:20]}%")).first()
        if not client:
            skipped += 1
            continue

        try:
            if gmv is not None:
                client.gmv = gmv
            if mrr is not None:
                client.mrr = mrr
            if segment_raw:
                from airtable_sync import _normalize_segment
                client.segment = _normalize_segment(segment_raw)
            if manager_raw and "@" in manager_raw:
                client.manager_email = manager_raw.lower()
            if domain_raw:
                for proto in ("https://", "http://"):
                    if domain_raw.startswith(proto):
                        domain_raw = domain_raw[len(proto):]
                client.domain = domain_raw.rstrip("/")
            client.last_sync_at = datetime.utcnow()
            updated += 1
        except Exception as exc:
            errors.append(f"{client_name}: {exc}")
            skipped += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        return {"ok": False, "error": f"DB error: {exc}", "updated": updated, "errors": errors[:5]}

    return {"ok": True, "rows": len(rows) - 1, "updated": updated,
            "skipped": skipped, "errors": errors[:5]}


async def push_churn_to_sheet(db) -> bool:
    """Push current churn risk data to Google Sheets (if writable via service account).

    Requires GOOGLE_SHEETS_CREDS env var with service account JSON.
    Returns True on success.
    """
    if not SHEETS_CREDS_JSON:
        logger.warning("push_churn_to_sheet: GOOGLE_SHEETS_CREDS not configured")
        return False

    try:
        import json as _json
        creds = _json.loads(SHEETS_CREDS_JSON)
    except Exception as e:
        logger.error("push_churn_to_sheet: invalid GOOGLE_SHEETS_CREDS JSON: %s", e)
        return False

    from models import Client, ChurnScore
    from datetime import datetime

    # Gather churn risk data
    scores = db.query(ChurnScore).join(Client, ChurnScore.client_id == Client.id).all()
    if not scores:
        return True  # nothing to push

    # Prepare rows
    header = ["Клиент", "Сегмент", "Риск (%)", "Уровень риска", "Объяснение", "Обновлено"]
    data_rows = [header]
    for sc in scores:
        client = sc.client
        data_rows.append([
            client.name if client else "—",
            client.segment if client else "—",
            str(round(sc.score, 1)) if sc.score is not None else "—",
            sc.risk_level or "—",
            (sc.explanation or "")[:200],
            sc.calculated_at.strftime("%Y-%m-%d %H:%M") if sc.calculated_at else "—",
        ])

    # Use Sheets API v4 to write
    token = await _get_service_account_token(creds, ["https://www.googleapis.com/auth/spreadsheets"])
    if not token:
        return False

    range_notation = f"A1:F{len(data_rows)}"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{CHURN_SHEET_ID}/values/{range_notation}"
    async with httpx.AsyncClient(timeout=30) as hx:
        try:
            resp = await hx.put(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params={"valueInputOption": "RAW"},
                json={"values": data_rows},
            )
            if resp.status_code == 200:
                logger.info("push_churn_to_sheet: pushed %d rows", len(data_rows) - 1)
                return True
            logger.error("push_churn_to_sheet error: %d %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("push_churn_to_sheet exception: %s", e)
    return False


async def _get_service_account_token(creds: dict, scopes: list) -> Optional[str]:
    """Get OAuth2 access token for Google service account (JWT flow)."""
    try:
        import time
        import json as _json
        import base64
        import hashlib
        import hmac

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": creds.get("client_email", ""),
            "scope": " ".join(scopes),
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        def _b64(data) -> str:
            if isinstance(data, dict):
                data = _json.dumps(data, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        unsigned = f"{_b64(header)}.{_b64(payload)}"

        # Sign with RS256 using cryptography library
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            key_pem = creds.get("private_key", "").encode()
            private_key = serialization.load_pem_private_key(key_pem, password=None)
            signature = private_key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
            jwt_token = f"{unsigned}.{_b64(signature)}"
        except ImportError:
            logger.warning("cryptography package not installed; cannot sign JWT for Google Sheets")
            return None

        async with httpx.AsyncClient(timeout=15) as hx:
            resp = await hx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": jwt_token,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
            logger.error("Google token error: %d %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("_get_service_account_token: %s", e)
    return None
