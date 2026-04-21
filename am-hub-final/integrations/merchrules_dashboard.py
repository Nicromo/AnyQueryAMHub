"""
integrations/merchrules_dashboard.py — синки синонимов/whitelist/blacklist/merch-rules из дашборда Merchrules.

Все функции используют тот же auth-flow что и merchrules_sync.py
(через httpx + /backend-v2/auth/login → Bearer token).

Endpoints пробуются с несколькими вариантами путей (backend-v2 API не везде задокументирован),
ответы нормализуются к единому виду. При несоответствии структуры — возвращается пустой список
с warning в лог, клиент UI отображает «источник не настроен».
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

BASE_URL = __import__("os").environ.get("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
_TIMEOUT = 20


# Пары (endpoint path candidates) — пробуем по очереди пока не получим 200.
_PATHS = {
    "synonyms":   ["/backend-v2/sites/{site_id}/synonyms",
                   "/backend-v2/synonyms?site_id={site_id}",
                   "/backend/synonyms?site_id={site_id}"],
    "whitelist":  ["/backend-v2/sites/{site_id}/whitelist",
                   "/backend-v2/whitelist?site_id={site_id}"],
    "blacklist":  ["/backend-v2/sites/{site_id}/blacklist",
                   "/backend-v2/blacklist?site_id={site_id}"],
    "merch_rules":["/backend-v2/sites/{site_id}/merch-rules",
                   "/backend-v2/sites/{site_id}/rules",
                   "/backend-v2/merch-rules?site_id={site_id}"],
}


def _extract_list(body: Any) -> List[dict]:
    """Достаём список из разных форм ответа: [..] | {items:[]} | {data:[]} | {rules:[]}."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "data", "results", "rules", "synonyms", "whitelist", "blacklist", "entries"):
            v = body.get(key)
            if isinstance(v, list):
                return v
    return []


async def _fetch_one(client: httpx.AsyncClient, token: str, kind: str, site_id: str) -> List[dict]:
    for path in _PATHS[kind]:
        url = BASE_URL + path.format(site_id=site_id)
        try:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
        except Exception as e:
            logger.debug(f"{kind} {url} exc: {e}")
            continue
        if r.status_code == 200:
            return _extract_list(r.json()) or []
        if r.status_code in (404, 405):
            continue
        logger.warning(f"{kind} {url} → HTTP {r.status_code}")
    return []


async def fetch_all(login: str, password: str, site_id: str) -> Dict[str, List[dict]]:
    """Забирает все 4 сущности дашборда за один заход. Возвращает пустые списки если не найдено."""
    from merchrules_sync import get_auth_token
    async with httpx.AsyncClient() as hx:
        token = await get_auth_token(hx, login, password)
        if not token:
            logger.warning("merchrules dashboard: auth failed")
            return {k: [] for k in _PATHS}
        result = {}
        for kind in _PATHS:
            result[kind] = await _fetch_one(hx, token, kind, site_id)
    return result


# ── Upsert в БД ──────────────────────────────────────────────────────────────

def _norm_syn(r: dict) -> Optional[dict]:
    term = r.get("term") or r.get("word") or r.get("query") or r.get("key")
    syns = r.get("synonyms") or r.get("values") or r.get("variants") or []
    if not term:
        return None
    return {
        "merchrules_id": str(r.get("id") or r.get("_id") or ""),
        "term": str(term),
        "synonyms": syns if isinstance(syns, list) else [str(syns)],
        "is_active": bool(r.get("active", r.get("is_active", True))),
    }


def _norm_wl(r: dict) -> Optional[dict]:
    q = r.get("query") or r.get("phrase") or r.get("term")
    if not q:
        return None
    return {
        "merchrules_id": str(r.get("id") or r.get("_id") or ""),
        "query": str(q),
        "product_id": str(r.get("product_id") or r.get("sku") or "") or None,
        "product_name": r.get("product_name") or r.get("title"),
        "position": r.get("position") or r.get("pos"),
        "is_active": bool(r.get("active", r.get("is_active", True))),
    }


def _norm_bl(r: dict) -> Optional[dict]:
    q = r.get("query") or r.get("phrase") or r.get("term")
    if not q:
        return None
    return {
        "merchrules_id": str(r.get("id") or r.get("_id") or ""),
        "query": str(q),
        "product_id": str(r.get("product_id") or r.get("sku") or "") or None,
        "product_name": r.get("product_name") or r.get("title"),
        "is_active": bool(r.get("active", r.get("is_active", True))),
    }


def _norm_rule(r: dict) -> Optional[dict]:
    name = r.get("name") or r.get("title")
    if not name:
        return None
    return {
        "merchrules_id": str(r.get("id") or r.get("_id") or ""),
        "name": str(name),
        "rule_type": r.get("type") or r.get("kind") or r.get("rule_type"),
        "status": r.get("status") or ("active" if r.get("active", True) else "inactive"),
        "priority": int(r.get("priority") or 0),
        "config": {k: v for k, v in r.items()
                    if k not in ("id", "_id", "name", "title", "type", "kind", "status", "priority")},
    }


def upsert_for_client(db: Session, client, bundle: Dict[str, List[dict]]) -> Dict[str, int]:
    """Апсёрт всех 4 сущностей. Возвращает счётчики {kind: count}."""
    from models import (
        ClientSynonym, ClientWhitelistEntry, ClientBlacklistEntry, ClientMerchRule,
    )
    now = datetime.utcnow()
    counts = {"synonyms": 0, "whitelist": 0, "blacklist": 0, "merch_rules": 0}

    # Synonyms
    seen_ids = set()
    for raw in bundle.get("synonyms") or []:
        n = _norm_syn(raw)
        if not n:
            continue
        row = None
        if n["merchrules_id"]:
            row = (db.query(ClientSynonym)
                     .filter(ClientSynonym.client_id == client.id,
                             ClientSynonym.merchrules_id == n["merchrules_id"])
                     .first())
        if not row:
            row = (db.query(ClientSynonym)
                     .filter(ClientSynonym.client_id == client.id,
                             ClientSynonym.term == n["term"])
                     .first())
        if not row:
            row = ClientSynonym(client_id=client.id)
            db.add(row)
        row.merchrules_id = n["merchrules_id"] or row.merchrules_id
        row.term = n["term"]
        row.synonyms = n["synonyms"]
        row.is_active = n["is_active"]
        row.last_synced = now
        if row.merchrules_id:
            seen_ids.add(row.merchrules_id)
        counts["synonyms"] += 1

    # Whitelist
    for raw in bundle.get("whitelist") or []:
        n = _norm_wl(raw)
        if not n:
            continue
        row = None
        if n["merchrules_id"]:
            row = (db.query(ClientWhitelistEntry)
                     .filter(ClientWhitelistEntry.client_id == client.id,
                             ClientWhitelistEntry.merchrules_id == n["merchrules_id"])
                     .first())
        if not row:
            row = ClientWhitelistEntry(client_id=client.id)
            db.add(row)
        for k, v in n.items():
            setattr(row, k, v)
        row.last_synced = now
        counts["whitelist"] += 1

    # Blacklist
    for raw in bundle.get("blacklist") or []:
        n = _norm_bl(raw)
        if not n:
            continue
        row = None
        if n["merchrules_id"]:
            row = (db.query(ClientBlacklistEntry)
                     .filter(ClientBlacklistEntry.client_id == client.id,
                             ClientBlacklistEntry.merchrules_id == n["merchrules_id"])
                     .first())
        if not row:
            row = ClientBlacklistEntry(client_id=client.id)
            db.add(row)
        for k, v in n.items():
            setattr(row, k, v)
        row.last_synced = now
        counts["blacklist"] += 1

    # Merch Rules (ClientMerchRule уже существует)
    for raw in bundle.get("merch_rules") or []:
        n = _norm_rule(raw)
        if not n:
            continue
        row = None
        if n["merchrules_id"]:
            row = (db.query(ClientMerchRule)
                     .filter(ClientMerchRule.client_id == client.id,
                             ClientMerchRule.merchrules_id == n["merchrules_id"])
                     .first())
        if not row:
            row = (db.query(ClientMerchRule)
                     .filter(ClientMerchRule.client_id == client.id,
                             ClientMerchRule.name == n["name"])
                     .first())
        if not row:
            row = ClientMerchRule(client_id=client.id)
            db.add(row)
        row.merchrules_id = n["merchrules_id"] or row.merchrules_id
        row.name = n["name"]
        row.rule_type = n["rule_type"]
        row.status = n["status"]
        row.priority = n["priority"]
        row.config = n["config"]
        row.last_synced = now
        counts["merch_rules"] += 1

    db.commit()
    return counts


async def sync_client(db: Session, client, login: str, password: str) -> Dict[str, int]:
    """Верхнеуровневая функция: fetch_all + upsert. Использует merchrules_account_id клиента."""
    if not client.merchrules_account_id:
        return {"error": "no merchrules_account_id"}
    bundle = await fetch_all(login, password, str(client.merchrules_account_id))
    return upsert_for_client(db, client, bundle)
