"""
Интеграция с MerchRules. Авторизация через login/password.
Синхронизирует встречи и задачи из AM Hub → Merchrules.

Кредсы передаются явно (из профиля менеджера), fallback — env-переменные.
"""
import os, io, csv, logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")

# Кэш токенов по login (чтобы не авторизовываться каждый раз)
_token_cache: dict[str, str] = {}


def _get_default_creds() -> tuple[str, str]:
    return os.getenv("MERCHRULES_LOGIN", ""), os.getenv("MERCHRULES_PASSWORD", "")


async def _get_token(client: httpx.AsyncClient, login: str, password: str) -> Optional[str]:
    if not login or not password:
        return None
    cache_key = f"{login}:{password[:8]}"
    if cache_key in _token_cache:
        return _token_cache[cache_key]
    try:
        r = await client.post(
            f"{MERCHRULES_URL}/backend-v2/auth/login",
            json={"username": login, "password": password},
            timeout=10,
        )
        if r.status_code == 200:
            body = r.json()
            token = body.get("token") or body.get("access_token") or body.get("accessToken", "")
            if token:
                _token_cache[cache_key] = token
            return token
        logger.warning("MR login failed %s %s: %s", login, r.status_code, r.text[:150])
    except Exception as e:
        logger.warning("MR login error (%s): %s", login, e)
    return None


def invalidate_token(login: str = "", password: str = ""):
    """Сбрасываем кэш токена (например после 401)."""
    if login:
        key = f"{login}:{password[:8]}"
        _token_cache.pop(key, None)
    else:
        _token_cache.clear()


async def push_tasks_csv(site_ids: list[str], tasks: list[dict],
                          login: str = "", password: str = "") -> dict:
    if not tasks or not site_ids:
        return {"ok": False, "error": "нет задач или site_ids"}
    if not login:
        login, password = _get_default_creds()

    async with httpx.AsyncClient(timeout=30) as hx:
        token = await _get_token(hx, login, password)
        if not token:
            return {"ok": False, "error": "Авторизация Merchrules не удалась — проверь логин/пароль в Профиле"}
        headers = {"Authorization": f"Bearer {token}"}
        uploaded, errors = [], []
        for site_id in site_ids:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["title", "description", "status", "priority",
                                                 "team", "task_type", "assignee", "product",
                                                 "link", "due_date"])
            w.writeheader()
            for t in tasks:
                w.writerow({
                    "title":       t.get("title", t.get("text", "")),
                    "description": t.get("description", ""),
                    "status":      t.get("status", "plan"),
                    "priority":    t.get("priority", "medium"),
                    "team":        t.get("team", ""),
                    "task_type":   t.get("task_type", ""),
                    "assignee":    "any",
                    "product":     t.get("product", "any_query_web"),
                    "link":        t.get("link", ""),
                    "due_date":    t.get("due_date", ""),
                })
            try:
                r = await hx.post(
                    f"{MERCHRULES_URL}/backend-v2/import/tasks/csv",
                    params={"site_id": site_id},
                    files={"file": ("tasks.csv", io.BytesIO(buf.getvalue().encode()), "text/csv")},
                    headers=headers,
                    timeout=20,
                )
                if r.status_code in (200, 201):
                    uploaded.append(site_id)
                else:
                    errors.append({"site_id": site_id, "error": r.text[:150]})
                    if r.status_code == 401:
                        invalidate_token(login, password)
            except Exception as e:
                errors.append({"site_id": site_id, "error": str(e)})

    return {"ok": bool(uploaded), "uploaded": uploaded, "errors": errors}


async def push_meeting(site_ids: list[str], meeting_date: str, meeting_type: str,
                       summary: str, mood: str, next_meeting: Optional[str],
                       login: str = "", password: str = "") -> dict:
    if not site_ids:
        return {"ok": False, "error": "нет site_ids"}
    if not login:
        login, password = _get_default_creds()

    async with httpx.AsyncClient(timeout=20) as hx:
        token = await _get_token(hx, login, password)
        if not token:
            return {"ok": False, "error": "Авторизация Merchrules не удалась — проверь логин/пароль в Профиле"}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        results = []
        for site_id in site_ids:
            try:
                r = await hx.post(
                    f"{MERCHRULES_URL}/backend-v2/meetings",
                    json={"site_id": site_id, "date": meeting_date, "type": meeting_type,
                          "summary": summary, "mood": mood, "next_date": next_meeting},
                    headers=headers,
                    timeout=15,
                )
                results.append({"site_id": site_id, "ok": r.status_code in (200, 201),
                                 "status": r.status_code})
                if r.status_code == 401:
                    invalidate_token(login, password)
            except Exception as e:
                results.append({"site_id": site_id, "ok": False, "error": str(e)})

    return {"ok": any(r.get("ok") for r in results), "results": results}


async def sync_meeting_to_merchrules(client_name: str, meeting_date: str, meeting_type: str,
                                     summary: str, mood: str, next_meeting: Optional[str],
                                     aq_tasks: list[dict], client_tasks: list[dict],
                                     site_ids: str = "",
                                     login: str = "", password: str = "") -> dict:
    """
    Главная точка входа. login/password — кредсы текущего менеджера.
    Если не переданы — берём из env (глобальный fallback).
    """
    if not login:
        login, password = _get_default_creds()
    if not login or not password:
        return {"ok": False, "skipped": True,
                "note": "Укажи логин и пароль Merchrules в разделе Профиль"}

    ids = [s.strip() for s in site_ids.split(",") if s.strip()]
    if not ids:
        return {"ok": False, "error": f"Нет site_ids для '{client_name}' — добавь в карточке клиента"}

    meeting_result = await push_meeting(ids, meeting_date, meeting_type, summary, mood,
                                         next_meeting, login=login, password=password)
    tasks_to_push = [
        {"title": t["text"], "due_date": t.get("due_date"), "status": "plan"}
        for t in aq_tasks if t.get("text")
    ]
    tasks_result = (
        await push_tasks_csv(ids, tasks_to_push, login=login, password=password)
        if tasks_to_push
        else {"ok": True, "uploaded": [], "errors": []}
    )
    return {
        "ok": meeting_result["ok"] or tasks_result["ok"],
        "meeting": meeting_result,
        "tasks": tasks_result,
    }
