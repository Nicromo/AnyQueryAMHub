from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, User, Task, Meeting, NPSEntry, UpsellEvent, RoadmapItem

logger = logging.getLogger(__name__)
router = APIRouter()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("API_GROQ", "")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")

_AI_SUGGESTIONS_STORE: dict = {}


def _require_user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


def _call_ai(system: str, prompt: str, max_tokens: int = 2500) -> str:
    import httpx
    if GROQ_API_KEY:
        try:
            with httpx.Client(timeout=60) as hx:
                resp = hx.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                    },
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Groq failed, trying Qwen: %s", e)

    if QWEN_API_KEY:
        import httpx
        try:
            with httpx.Client(timeout=60) as hx:
                resp = hx.post(
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                    json={
                        "model": "qwen-plus",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                    },
                    headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
                )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Qwen failed: %s", e)

    raise RuntimeError("No AI provider available (set GROQ_API_KEY or QWEN_API_KEY)")


@router.post("/api/clients/{client_id}/roadmap/generate")
async def generate_roadmap(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)

    now = datetime.utcnow()
    since_90 = now - timedelta(days=90)

    open_tasks = (
        db.query(Task)
        .filter(Task.client_id == client_id, Task.status != "done")
        .order_by(Task.priority.desc(), Task.due_date.asc().nullslast())
        .limit(15)
        .all()
    )
    meetings_90 = (
        db.query(Meeting)
        .filter(Meeting.client_id == client_id, Meeting.date >= since_90)
        .order_by(Meeting.date.desc())
        .all()
    )
    last_nps = (
        db.query(NPSEntry)
        .filter(NPSEntry.client_id == client_id)
        .order_by(NPSEntry.recorded_at.desc())
        .first()
    )
    upsell_events = (
        db.query(UpsellEvent)
        .filter(
            UpsellEvent.client_id == client_id,
            UpsellEvent.status.in_(["identified", "in_progress"]),
        )
        .all()
    )

    tasks_text = "\n".join(
        f"- [{t.priority}] {t.title}"
        for t in open_tasks
    ) or "Нет открытых задач"

    meetings_text = (
        f"{len(meetings_90)} встреч за 90 дней. "
        + ("Последняя: " + meetings_90[0].date.strftime("%d.%m.%Y") if meetings_90 else "Встреч нет.")
    )

    nps_text = f"NPS: {last_nps.score}" if last_nps else "NPS: нет данных"
    upsell_text = (
        f"Активных апсейл-возможностей: {len(upsell_events)} "
        + "(сумма delta: " + str(sum(float(e.delta or 0) for e in upsell_events)) + ")"
        if upsell_events else "Апсейл-возможностей нет"
    )

    prompt = f"""Клиент: {c.name} | Сегмент: {c.segment or '—'} | MRR: {c.mrr or 0} | GMV: {c.gmv or 0}
Health Score: {c.health_score or 0:.2f}
Контракт до: {c.contract_end.isoformat() if c.contract_end else '—'}
{nps_text}
{meetings_text}
{upsell_text}

Открытые задачи:
{tasks_text}

Сгенерируй 4-квартальный роадмап для этого клиента на ближайший год.
Верни строго JSON (без обёртки ```json```) следующей структуры:
[
  {{
    "quarter": "Q1",
    "items": [
      {{"id": "q1_1", "title": "...", "priority": "high|medium|low", "category": "product|process|growth|retention|technical"}},
      ...
    ]
  }},
  {{"quarter": "Q2", "items": [...]}},
  {{"quarter": "Q3", "items": [...]}},
  {{"quarter": "Q4", "items": [...]}}
]
По 3-5 пунктов на квартал. Пиши по-русски, конкретно, без шаблонных фраз."""

    system = (
        "Ты — AI-ассистент Account Manager'а AnyQuery. "
        "Составляй реалистичные квартальные роадмапы на основе данных клиента. "
        "Отвечай строго JSON без дополнительного текста."
    )

    try:
        raw = _call_ai(system, prompt, max_tokens=2500)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        roadmap = json.loads(raw)
    except Exception as e:
        logger.error("Roadmap AI generation failed for client %s: %s", client_id, e)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {e}")

    _AI_SUGGESTIONS_STORE[client_id] = {
        "generated_at": now.isoformat(),
        "generated_by": user.email,
        "roadmap": roadmap,
    }

    return {
        "ok": True,
        "client_id": client_id,
        "generated_at": now.isoformat(),
        "roadmap": roadmap,
    }


@router.get("/api/clients/{client_id}/roadmap/suggestions")
async def get_roadmap_suggestions(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)

    stored = _AI_SUGGESTIONS_STORE.get(client_id)
    if not stored:
        return {
            "client_id": client_id,
            "generated_at": None,
            "roadmap": [],
        }
    return {
        "client_id": client_id,
        "generated_at": stored.get("generated_at"),
        "generated_by": stored.get("generated_by"),
        "roadmap": stored.get("roadmap", []),
    }


@router.post("/api/clients/{client_id}/roadmap/apply")
async def apply_roadmap_suggestions(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    c = db.query(Client).filter(Client.id == client_id).first()
    if not c:
        raise HTTPException(status_code=404)

    data = await request.json()
    selected_ids: List[str] = data.get("items", [])
    if not selected_ids:
        raise HTTPException(status_code=400, detail="items list is required")

    stored = _AI_SUGGESTIONS_STORE.get(client_id)
    if not stored:
        raise HTTPException(status_code=404, detail="No AI suggestions found. Generate roadmap first.")

    roadmap: List[dict] = stored.get("roadmap", [])

    all_items: dict = {}
    for quarter_block in roadmap:
        quarter = quarter_block.get("quarter", "")
        for item in quarter_block.get("items", []):
            item_id = item.get("id")
            if item_id:
                all_items[item_id] = (quarter, item)

    PRIORITY_ORDER = {"high": 1, "medium": 2, "low": 3}
    max_order = db.query(RoadmapItem).count()

    created = []
    for item_id in selected_ids:
        if item_id not in all_items:
            continue
        quarter, item = all_items[item_id]
        column_key = quarter.lower()
        column_title = quarter
        tone = "info"
        priority = item.get("priority", "medium")
        if priority == "high":
            tone = "warn"
        elif priority == "low":
            tone = "ok"

        max_order += 1
        ri = RoadmapItem(
            column_key=column_key,
            column_title=column_title,
            tone=tone,
            title=item.get("title", ""),
            description=f"[AI] Категория: {item.get('category', '—')} | Приоритет: {priority} | Клиент: {c.name}",
            order_idx=max_order,
            author_id=user.id,
        )
        db.add(ri)
        created.append({
            "item_id": item_id,
            "title": ri.title,
            "quarter": quarter,
            "priority": priority,
        })

    db.commit()
    return {
        "ok": True,
        "created_count": len(created),
        "created": created,
    }
