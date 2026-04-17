"""
Полная синхронизация клиентов из Airtable → PostgreSQL.
Запуск: python sync_airtable_clients.py [--dry-run]
"""
import os, sys, json, time, urllib.request, urllib.parse, logging
from pathlib import Path
from datetime import datetime

# Загружаем .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s",
                    stream=sys.stdout)
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv

TOKEN   = os.getenv("AIRTABLE_TOKEN") or os.getenv("AIRTABLE_PAT")
BASE_ID = "appEAS1rPKpevoIel"
TABLE_ID = "tblIKAi1gcFayRJTn"
VIEW_ID  = "viwRIS9GBXNQXqCf2"  # main view — 1900 records

if not TOKEN:
    log.error("AIRTABLE_TOKEN not set in .env")
    sys.exit(1)

# ── Airtable helpers ──────────────────────────────────────────────────────────

def airtable_get(path: str, params: dict | None = None) -> dict:
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "AMHub-Sync/1.0",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                raise
            log.warning(f"Retry {attempt+1}: {e}")
            time.sleep(2 ** attempt)


def fetch_all_records() -> list[dict]:
    """Paginate through ALL Airtable records."""
    records = []
    params = {"view": VIEW_ID, "pageSize": 100}
    page = 0
    while True:
        page += 1
        log.info(f"Fetching page {page} ({len(records)} records so far)...")
        data = airtable_get("", params)
        batch = data.get("records", [])
        records.extend(batch)
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset
        time.sleep(0.2)  # be polite to Airtable API
    log.info(f"Total records fetched: {len(records)}")
    return records


# ── Field mapping ─────────────────────────────────────────────────────────────

def safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0

def safe_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None

def parse_date(v) -> str | None:
    if not v:
        return None
    # Airtable returns YYYY-MM-DD or ISO datetime
    if isinstance(v, str):
        return v[:10]  # take YYYY-MM-DD part
    return None

def map_segment(raw: str | None) -> str:
    """Map Airtable 'Размер клиента' to our segment codes."""
    if not raw:
        return "SMB"
    v = str(raw).strip().upper()
    mapping = {
        "ENT": "ENT",
        "SME": "SME",
        "SMB": "SMB",
        "SS": "SS",
        "SELF-SERVICE": "SS",
        "ENTERPRISE": "ENT",
    }
    return mapping.get(v, v[:10])

def compute_health(f: dict) -> float:
    """Health score 0-1 from churn predictions and stage."""
    churn_2m = safe_float(f.get("Churn Pred 2m"))
    churn_6m = safe_float(f.get("Churn Pred 6m"))
    churn_aq  = bool(f.get("Churn AQ"))
    churn_rec = bool(f.get("Churn Recs"))

    # If fully churned — low health
    if churn_aq and churn_rec:
        return 0.1
    if churn_aq or churn_rec:
        return 0.25

    # Derive from churn probability (0-10 scale → invert to 0-1)
    pred = max(churn_2m, churn_6m)
    if pred > 0:
        base = max(0.1, 1.0 - pred / 10.0)
    else:
        base = 0.75  # no prediction = neutral

    # Adjust by Customer Stage
    stage = str(f.get("Customer Stage AQ", "") or "").lower()
    risk_map = {
        "churn": -0.4,
        "нужно внимание": -0.2,
        "под угрозой": -0.3,
        "стабильная работа": +0.1,
        "развитие": +0.15,
        "онбординг": 0.0,
    }
    for key, delta in risk_map.items():
        if key in stage:
            base = min(1.0, max(0.05, base + delta))
            break

    return round(base, 2)


def record_to_row(rec: dict) -> dict:
    f = rec["fields"]

    name = safe_str(f.get("Account"))
    if not name:
        return None  # skip records without account name

    # MRR — sum all product MRRs
    aq_mrr   = safe_float(f.get("AQ MRR"))
    app_mrr  = safe_float(f.get("APP MRR"))
    recs_mrr = safe_float(f.get("Recs MRR"))
    rev_mrr  = safe_float(f.get("AnyReviews MRR"))
    img_mrr  = safe_float(f.get("AnyImages MRR"))
    ac_mrr   = safe_float(f.get("AC MRR"))
    total_mrr = aq_mrr + app_mrr + recs_mrr + rev_mrr + img_mrr + ac_mrr

    # Latest NPS — take most recent quarter available
    nps = None
    for fld in ["NPS оценка Q2 2026", "NPS оценка Q1 2026",
                "NPS оценка Q4 2026", "NPS оценка Q3 2026"]:
        v = f.get(fld)
        if v is not None:
            nps = safe_float(v)
            break

    # Products list
    products = f.get("Products") or []
    if isinstance(products, str):
        products = [products]

    # Churn risk level
    churn_risk_2m = (f.get("Churn Risk 2m") or [None])[0]
    churn_risk_6m = (f.get("Churn Risk 6m") or [None])[0]

    # CSI (Customer Satisfaction Index) — latest
    csi = None
    for fld in ["CSI - Q1 - 2025", "CSI - Q4 - 2024", "CSI - Q3 - 2024"]:
        v = f.get(fld)
        if v is not None:
            csi = safe_float(v)
            break

    # Domain cleanup
    raw_url = safe_str(f.get("URL"))
    domain = raw_url
    if raw_url:
        # strip protocol and trailing slash
        for proto in ("https://", "http://"):
            if raw_url.startswith(proto):
                domain = raw_url[len(proto):]
        domain = domain.rstrip("/")

    # Build integration_metadata for rich data
    metadata = {
        "customer_stage_aq":    safe_str(f.get("Customer Stage AQ")),
        "customer_stage_recs":  safe_str(f.get("Customer Stage Recs")),
        "customer_stage_app":   safe_str(f.get("Customer Stage AQ APP")),
        "churn_aq":             bool(f.get("Churn AQ")),
        "churn_recs":           bool(f.get("Churn Recs")),
        "churn_risk_2m":        churn_risk_2m,
        "churn_risk_6m":        churn_risk_6m,
        "churn_pred_2m":        safe_float(f.get("Churn Pred 2m")),
        "churn_pred_6m":        safe_float(f.get("Churn Pred 6m")),
        "churn_reasons_2m":     safe_str(f.get("Churn Reasons 2m")),
        "churn_reasons_6m":     safe_str(f.get("Churn Reasons 6m")),
        "products":             products,
        "package":              safe_str(f.get("Пакет клиента")),
        "vertical":             (f.get("Вертикаль") or [None])[0],
        "contract_type":        (f.get("Тип договора") or [None])[0],
        "aq_mrr":               aq_mrr,
        "app_mrr":              app_mrr,
        "recs_mrr":             recs_mrr,
        "reviews_mrr":          rev_mrr,
        "images_mrr":           img_mrr,
        "ac_mrr":               ac_mrr,
        "csi":                  csi,
        "comment":              safe_str(f.get("Комментарий к статусу")),
        "inn":                  safe_str(f.get("ИНН")),
        "checkup_url":          safe_str(f.get("Check-up CS")),
        "datalens_url":         safe_str(f.get("Ссылка на datalens")),
        "telegram_chat":        safe_str(f.get("Telegram chat link")),
        "sales_manager":        safe_str(f.get("Сейлз менеджер")),
        "churn_risk_change_2m": safe_str(f.get("Risk Change 2m")),
        "next_comm_date":       parse_date(f.get("Дата следующей коммуникации NEW")),
        "churn_date_aq":        parse_date(f.get("Дата ухода AQ")),
        "churn_date_recs":      parse_date(f.get("Дата ухода Recs")),
        "threat_probability":   safe_str(f.get("Вероятность угрозы")),
        "incidents_30d":        int(f.get("Кол-во инцидентов в TS (30 дней)") or 0),
    }
    # Remove None values to keep metadata lean
    metadata = {k: v for k, v in metadata.items() if v is not None and v != 0}

    return {
        "airtable_record_id":     rec["id"],
        "name":                   name,
        "domain":                 domain,
        "merchrules_account_id":  safe_str(f.get("Site ID")),
        "site_ids":               [safe_str(f.get("Site ID"))] if f.get("Site ID") else [],
        "manager_email":          safe_str(f.get("CSM")),
        "segment":                map_segment(f.get("Размер клиента")),
        "mrr":                    total_mrr,
        "health_score":           compute_health(f),
        "nps_last":               nps,
        "last_meeting_date":      parse_date(f.get("Дата последней коммуникации")),
        "last_sync_at":           datetime.utcnow().isoformat(),
        "integration_metadata":   metadata,
        "open_tickets":           int(f.get("Кол-во инцидентов в TS (30 дней)") or 0),
    }


# ── Database sync ─────────────────────────────────────────────────────────────

def sync_to_db(rows: list[dict]):
    from database import engine
    from sqlalchemy import text

    log.info(f"Syncing {len(rows)} rows to PostgreSQL...")

    upsert_sql = text("""
        INSERT INTO clients (
            airtable_record_id, name, domain, merchrules_account_id, site_ids,
            manager_email, segment, mrr, health_score, nps_last,
            last_meeting_date, last_sync_at, integration_metadata, open_tickets
        ) VALUES (
            :airtable_record_id, :name, :domain, :merchrules_account_id, :site_ids,
            :manager_email, :segment, :mrr, :health_score, :nps_last,
            :last_meeting_date, :last_sync_at, :integration_metadata, :open_tickets
        )
        ON CONFLICT (airtable_record_id) DO UPDATE SET
            name                  = EXCLUDED.name,
            domain                = EXCLUDED.domain,
            merchrules_account_id = EXCLUDED.merchrules_account_id,
            site_ids              = EXCLUDED.site_ids,
            manager_email         = EXCLUDED.manager_email,
            segment               = EXCLUDED.segment,
            mrr                   = EXCLUDED.mrr,
            health_score          = EXCLUDED.health_score,
            nps_last              = EXCLUDED.nps_last,
            last_meeting_date     = EXCLUDED.last_meeting_date,
            last_sync_at          = EXCLUDED.last_sync_at,
            integration_metadata  = EXCLUDED.integration_metadata,
            open_tickets          = EXCLUDED.open_tickets
    """)

    inserted = skipped = 0

    # Use psycopg2 Json adapter for JSONB columns
    try:
        from psycopg2.extras import Json as PgJson
    except ImportError:
        PgJson = None

    def to_jsonb(v):
        if PgJson is not None:
            return PgJson(v)
        return json.dumps(v, ensure_ascii=False)

    # Process in chunks with individual transactions per row for fault tolerance
    for row in rows:
        try:
            with engine.begin() as conn:
                params = dict(row)
                params["site_ids"] = to_jsonb(params.get("site_ids") or [])
                params["integration_metadata"] = to_jsonb(params.get("integration_metadata") or {})
                # Ensure numeric types
                params["mrr"] = float(params.get("mrr") or 0)
                params["health_score"] = float(params.get("health_score") or 0)
                params["nps_last"] = float(params["nps_last"]) if params.get("nps_last") is not None else None
                params["open_tickets"] = int(params.get("open_tickets") or 0)
                conn.execute(upsert_sql, params)
                inserted += 1
                if inserted % 100 == 0:
                    log.info(f"  Progress: {inserted}/{len(rows)}")
        except Exception as e:
            err_short = str(e)[:120].replace("\n", " ")
            log.warning(f"Skip '{row.get('name')}': {err_short}")
            skipped += 1

    log.info(f"Done: {inserted} upserted, {skipped} skipped")
    return inserted, skipped


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== Airtable → PostgreSQL full sync ===")
    log.info(f"Dry-run: {DRY_RUN}")

    records = fetch_all_records()

    # Map records
    rows = []
    skipped_noname = 0
    for rec in records:
        row = record_to_row(rec)
        if row is None:
            skipped_noname += 1
        else:
            rows.append(row)

    log.info(f"Mapped: {len(rows)} valid rows, {skipped_noname} skipped (no Account name)")

    if DRY_RUN:
        log.info("DRY RUN — not writing to DB")
        for r in rows[:5]:
            log.info(f"  Sample: {r['name']!r} | {r['segment']} | MRR={r['mrr']} | CSM={r['manager_email']!r}")
        sys.exit(0)

    inserted, skipped = sync_to_db(rows)

    # Final stats
    from database import engine
    from sqlalchemy import text
    with engine.connect() as c:
        total = c.execute(text("SELECT COUNT(*) FROM clients")).fetchone()[0]
        by_seg = dict(c.execute(text(
            "SELECT segment, COUNT(*) FROM clients GROUP BY segment ORDER BY COUNT(*) DESC"
        )).fetchall())
    log.info(f"Final DB: {total} clients total")
    log.info(f"By segment: {by_seg}")
