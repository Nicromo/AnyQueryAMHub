"""Автосбор QBR-метрик по продуктам клиента из Merchrules."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def current_quarter(dt: datetime | None = None) -> str:
    dt = dt or datetime.utcnow()
    q = (dt.month - 1) // 3 + 1
    return f"Q{q}-{dt.year}"


def prev_quarter(q: str) -> str:
    # "Q1-2026" → "Q4-2025"
    num, year = q.split("-")
    num = int(num[1:])
    year = int(year)
    if num == 1:
        return f"Q4-{year - 1}"
    return f"Q{num - 1}-{year}"


def quarter_bounds(q: str) -> tuple[datetime, datetime]:
    num, year = q.split("-")
    num = int(num[1:])
    year = int(year)
    start_month = (num - 1) * 3 + 1
    start = datetime(year, start_month, 1)
    end_month = start_month + 3
    end_year = year + (1 if end_month > 12 else 0)
    end_month = ((end_month - 1) % 12) + 1
    end = datetime(end_year, end_month, 1)
    return start, end


async def collect_product_metrics(client_id: int, site_ids: list, products: list, period: str) -> Dict[str, Any]:
    """Собирает метрики по всем продуктам клиента за указанный квартал.

    Порядок источников:
      1) /backend-v2/api/v1/any-products/metrics?site_id=...&date_from=...&date_to=...
         — основной источник per-product (sort/recs/autocomplete/...).
      2) /api/report/agg/{siteId}/global?name=REVENUE_TOTAL,... — агрегаты
         за квартал, идут в ``totals``.

    Возвращает: {per_product: {code: {...}}, totals: {...}, period: str, collected_at: iso}.
    """
    from merchrules_sync import (
        fetch_any_products_metrics,
        fetch_report_agg,
        _REPORT_METRIC_NAMES_DEFAULT,
    )

    start, end = parse_quarter(period)
    date_from = start.date().isoformat()
    date_to = (end - timedelta(seconds=1)).date().isoformat()

    per_product: Dict[str, Dict[str, Any]] = {}
    totals: Dict[str, Any] = {"sites": len(site_ids), "products": len(products)}

    for site_id in site_ids or []:
        try:
            # 1) per-product метрики
            by_prod_resp = await fetch_any_products_metrics(
                site_id=str(site_id),
                date_from=date_from,
                date_to=date_to,
            )
            items = by_prod_resp.get("items") or by_prod_resp.get("products") or []
            if isinstance(items, dict):
                # Формат {code: {...}}
                items = [{"code": k, **(v if isinstance(v, dict) else {})} for k, v in items.items()]
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = (item.get("code") or item.get("platform") or item.get("name") or "unknown")
                agg = per_product.setdefault(str(code), {})
                for k, v in item.items():
                    if isinstance(v, (int, float)):
                        agg[k] = agg.get(k, 0) + v
                    elif k in ("code", "platform", "name", "status"):
                        agg.setdefault(k, v)

            # 2) агрегаты за период для totals
            agg_resp = await fetch_report_agg(
                site_id=str(site_id),
                date_from=date_from,
                date_to=date_to,
                names=list(_REPORT_METRIC_NAMES_DEFAULT),
            )
            for name in _REPORT_METRIC_NAMES_DEFAULT:
                v = agg_resp.get(name) if isinstance(agg_resp, dict) else None
                if isinstance(v, (int, float)):
                    totals[name] = totals.get(name, 0) + v
                elif isinstance(v, dict):
                    # Иногда Merchrules возвращает {value: N, ...}
                    vv = v.get("value") or v.get("total")
                    if isinstance(vv, (int, float)):
                        totals[name] = totals.get(name, 0) + vv
        except Exception:
            log.exception("collect_product_metrics site=%s failed", site_id)

    # Дополним ClientProduct-ами: статус/имя, если в merchrules ответе нет.
    for p in products or []:
        code = p.get("code") or "unknown"
        slot = per_product.setdefault(code, {})
        slot.setdefault("name", p.get("name"))
        slot.setdefault("status", p.get("status"))

    return {
        "period": period,
        "collected_at": datetime.utcnow().isoformat(),
        "totals": totals,
        "per_product": per_product,
    }


def compute_deltas(current: Dict[str, Any], previous: Dict[str, Any] | None) -> Dict[str, Any]:
    """Δ% к прошлому кварталу по числовым метрикам totals и per_product."""
    if not previous:
        return {}

    def delta(a, b):
        try:
            a = float(a)
            b = float(b)
            if b == 0:
                return None
            return round((a - b) / b * 100, 1)
        except Exception:
            return None

    res = {"totals": {}, "per_product": {}}
    for k, v in (current.get("totals") or {}).items():
        if isinstance(v, (int, float)):
            res["totals"][k] = delta(v, (previous.get("totals") or {}).get(k))
    for pcode, pm in (current.get("per_product") or {}).items():
        prev_p = (previous.get("per_product") or {}).get(pcode, {})
        res["per_product"][pcode] = {}
        for k, v in (pm or {}).items():
            if isinstance(v, (int, float)):
                res["per_product"][pcode][k] = delta(v, prev_p.get(k))
    return res


def ai_draft(metrics: Dict[str, Any], deltas: Dict[str, Any], client) -> Dict[str, List[str]]:
    """AI-черновик achievements/issues/next_goals из метрик. Фоллбек: простые правила."""
    try:
        from ai_assistant import generate_qbr_draft
        return generate_qbr_draft(client, metrics, deltas)
    except Exception as _e:
        import logging as _l
        _l.getLogger(__name__).warning(f"ai_draft fallback to heuristic: {_e}")
    # Фоллбек: эвристика
    ach, iss, goals = [], [], []
    totals = metrics.get("totals") or {}
    dtot = (deltas or {}).get("totals") or {}
    for k, dv in dtot.items():
        if dv is None:
            continue
        if dv > 5:
            ach.append(f"{k}: рост на {dv}%")
        elif dv < -5:
            iss.append(f"{k}: падение на {dv}%")
    for pcode in metrics.get("per_product") or {}:
        goals.append(f"Продукт {pcode}: план улучшений на следующий квартал")
    if not ach:
        ach = ["Метрики стабильны"]
    if not iss:
        iss = ["Существенных проблем не выявлено"]
    return {"achievements": ach, "issues": iss, "next_goals": goals[:5]}


async def collect_and_save(db: Session, client, quarter: Optional[str] = None, overwrite_text: bool = False) -> Dict[str, Any]:
    """Собрать метрики за квартал и сохранить в QBR. Текстовые поля не перезаписываем без overwrite_text."""
    from models import QBR, ClientProduct
    q = quarter or current_quarter()
    products = [{"code": p.code, "name": p.name, "status": p.status} for p in (client.products or [])]
    site_ids = client.site_ids or []

    current = await collect_product_metrics(client.id, site_ids, products, q)

    # Предыдущий квартал — возьмём из БД если есть
    prev_q = prev_quarter(q)
    prev_qbr = db.query(QBR).filter(QBR.client_id == client.id, QBR.quarter == prev_q).first()
    prev_metrics = (prev_qbr.metrics if prev_qbr and prev_qbr.metrics else None)

    current["deltas"] = compute_deltas(current, prev_metrics)

    # Год для записи QBR (модель требует year NOT NULL)
    try:
        _, _year_str = q.split("-")
        year_val = int(_year_str)
    except Exception:
        year_val = datetime.utcnow().year

    # Upsert QBR
    qbr = db.query(QBR).filter(QBR.client_id == client.id, QBR.quarter == q).first()
    if not qbr:
        qbr = QBR(
            client_id=client.id,
            quarter=q,
            year=year_val,
            date=datetime.utcnow(),
            status="draft",
            metrics=current,
            achievements=[],
            issues=[],
            next_quarter_goals=[],
        )
        db.add(qbr)
    else:
        qbr.metrics = current

    # AI-черновик
    draft = ai_draft(current, current.get("deltas") or {}, client)
    if overwrite_text or not qbr.achievements:
        qbr.achievements = draft.get("achievements", [])
    if overwrite_text or not qbr.issues:
        qbr.issues = draft.get("issues", [])
    if overwrite_text or not qbr.next_quarter_goals:
        qbr.next_quarter_goals = draft.get("next_goals", [])

    # last_qbr_date у клиента
    try:
        client.last_qbr_date = datetime.utcnow()
    except Exception:
        pass

    db.commit()

    # Уведомляем менеджера: бриф к QBR готов (можно открывать клиентскую карточку)
    try:
        if client.manager_email:
            from models import User as _User
            mgr = db.query(_User).filter(
                _User.email == client.manager_email, _User.is_active == True
            ).first()
            if mgr:
                from tg_notifications import notify_manager
                await notify_manager(db, mgr, "qbr_ready", {
                    "client": client.name,
                    "link": f"/design/client/{client.id}",
                }, related_type="client", related_id=client.id)
                db.commit()
    except Exception as _ne:
        log.warning(f"notify qbr_ready skipped: {_ne}")

    return {"ok": True, "quarter": q, "metrics": current, "qbr_id": qbr.id, "overwrote_text": overwrite_text}
