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
    Возвращает: {per_product: {code: {...}}, totals: {...}, period: str, collected_at: iso}.
    """
    # TODO: реальные per-product endpoints Merchrules неизвестны.
    # Пока используем общие analytics через fetch_account_analytics и get_client_metrics,
    # и распределяем по продуктам эвристикой (если в ответе есть разбивка по модулям).
    import httpx
    from merchrules_sync import get_client_metrics, get_auth_token
    try:
        from integrations.merchrules_extended import fetch_account_analytics, get_auth_token as _get_token
    except Exception:
        fetch_account_analytics = None

    per_product: Dict[str, Dict[str, Any]] = {}
    totals: Dict[str, Any] = {"sites": len(site_ids), "products": len(products)}

    for site_id in site_ids or []:
        try:
            m = await get_client_metrics(str(site_id))
            if m and isinstance(m, dict):
                # Сложим все числовые поля в totals
                for k, v in m.items():
                    if isinstance(v, (int, float)):
                        totals[k] = totals.get(k, 0) + v
                # Если в ответе есть разбивка по продуктам — распредели
                by_prod = m.get("by_product") or m.get("products") or {}
                if isinstance(by_prod, dict):
                    for pcode, pm in by_prod.items():
                        if not isinstance(pm, dict):
                            continue
                        agg = per_product.setdefault(pcode, {})
                        for k, v in pm.items():
                            if isinstance(v, (int, float)):
                                agg[k] = agg.get(k, 0) + v
        except Exception:
            log.exception("collect_product_metrics site=%s failed", site_id)

    # Если разбивки не получили — хотя бы обозначим продукты из ClientProduct
    for p in products or []:
        per_product.setdefault(p.get("code") or "unknown", {"name": p.get("name"), "status": p.get("status")})

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
    return {"ok": True, "quarter": q, "metrics": current, "qbr_id": qbr.id, "overwrote_text": overwrite_text}
