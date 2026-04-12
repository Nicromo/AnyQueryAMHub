"""
Интеграция с MerchRules. Авторизация через login/password.
Синхронизирует встречи и задачи из AM Hub → Merchrules.
"""
import os, io, csv, logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
MR_LOGIN       = os.getenv("MERCHRULES_LOGIN", "")
MR_PASSWORD    = os.getenv("MERCHRULES_PASSWORD", "")
_token_cache: dict = {"token": None}


async def _get_token(client: httpx.AsyncClient) -> Optional[str]:
    if _token_cache["token"]:
        return _token_cache["token"]
    if not MR_LOGIN or not MR_PASSWORD:
        return None
    try:
        r = await client.post(f"{MERCHRULES_URL}/backend-v2/auth/login",
                              json={"username": MR_LOGIN, "password": MR_PASSWORD}, timeout=10)
        if r.status_code == 200:
            body = r.json()
            token = body.get("token") or body.get("access_token") or body.get("accessToken", "")
            _token_cache["token"] = token
            return token
        logger.warning("MR login failed %s: %s", r.status_code, r.text[:150])
    except Exception as e:
        logger.warning("MR login error: %s", e)
    return None


async def push_tasks_csv(site_ids: list[str], tasks: list[dict]) -> dict:
    if not tasks or not site_ids:
        return {"ok": False, "error": "нет задач или site_ids"}
    async with httpx.AsyncClient(timeout=30) as hx:
        token = await _get_token(hx)
        if not token:
            return {"ok": False, "error": "Авторизация Merchrules не удалась"}
        headers = {"Authorization": f"Bearer {token}"}
        uploaded, errors = [], []
        for site_id in site_ids:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["title","description","status","priority",
                                                 "team","task_type","assignee","product","link","due_date"])
            w.writeheader()
            for t in tasks:
                w.writerow({"title": t.get("title", t.get("text","")),
                             "description": t.get("description",""), "status": t.get("status","plan"),
                             "priority": t.get("priority","medium"), "team": t.get("team",""),
                             "task_type": t.get("task_type",""), "assignee": "any",
                             "product": t.get("product","any_query_web"),
                             "link": t.get("link",""), "due_date": t.get("due_date","")})
            try:
                r = await hx.post(f"{MERCHRULES_URL}/backend-v2/import/tasks/csv",
                                  params={"site_id": site_id},
                                  files={"file": ("tasks.csv", io.BytesIO(buf.getvalue().encode()), "text/csv")},
                                  headers=headers, timeout=20)
                if r.status_code in (200, 201):
                    uploaded.append(site_id)
                else:
                    errors.append({"site_id": site_id, "error": r.text[:150]})
                    if r.status_code == 401:
                        _token_cache["token"] = None
            except Exception as e:
                errors.append({"site_id": site_id, "error": str(e)})
    return {"ok": bool(uploaded), "uploaded": uploaded, "errors": errors}


async def push_meeting(site_ids: list[str], meeting_date: str, meeting_type: str,
                       summary: str, mood: str, next_meeting: Optional[str]) -> dict:
    if not site_ids:
        return {"ok": False, "error": "нет site_ids"}
    async with httpx.AsyncClient(timeout=20) as hx:
        token = await _get_token(hx)
        if not token:
            return {"ok": False, "error": "Авторизация Merchrules не удалась"}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        results = []
        for site_id in site_ids:
            try:
                r = await hx.post(f"{MERCHRULES_URL}/backend-v2/meetings",
                                  json={"site_id": site_id, "date": meeting_date, "type": meeting_type,
                                        "summary": summary, "mood": mood, "next_date": next_meeting},
                                  headers=headers, timeout=15)
                results.append({"site_id": site_id, "ok": r.status_code in (200, 201), "status": r.status_code})
                if r.status_code == 401:
                    _token_cache["token"] = None
            except Exception as e:
                results.append({"site_id": site_id, "ok": False, "error": str(e)})
    return {"ok": any(r.get("ok") for r in results), "results": results}


async def sync_meeting_to_merchrules(client_name: str, meeting_date: str, meeting_type: str,
                                     summary: str, mood: str, next_meeting: Optional[str],
                                     aq_tasks: list[dict], client_tasks: list[dict],
                                     site_ids: str = "") -> dict:
    if not MR_LOGIN or not MR_PASSWORD:
        return {"ok": False, "skipped": True,
                "note": "MERCHRULES_LOGIN/PASSWORD не заданы — данные сохранены только в AM Hub"}
    ids = [s.strip() for s in site_ids.split(",") if s.strip()]
    if not ids:
        return {"ok": False, "error": f"Нет site_ids для '{client_name}' — добавь в карточке"}
    meeting_result = await push_meeting(ids, meeting_date, meeting_type, summary, mood, next_meeting)
    tasks_to_push = [{"title": t["text"], "due_date": t.get("due_date"), "status": "plan"}
                     for t in aq_tasks if t.get("text")]
    tasks_result = await push_tasks_csv(ids, tasks_to_push) if tasks_to_push else {"ok": True, "uploaded": [], "errors": []}
    return {"ok": meeting_result["ok"] or tasks_result["ok"],
            "meeting": meeting_result, "tasks": tasks_result}
