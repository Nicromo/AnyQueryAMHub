"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional
from datetime import datetime
import os
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Cookie, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from database import get_db
from models import Client, User, JiraIssue
from auth import decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


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


def _get_jira_creds(user: User) -> dict:
    settings = user.settings or {}
    return settings.get("jira") or {}


def _jira_client(creds: dict):
    from jira import JIRA
    return JIRA(
        server=creds["url"],
        basic_auth=(creds["email"], creds["api_token"]),
    )


def _issue_dict(issue: JiraIssue) -> dict:
    return {
        "id": issue.id,
        "client_id": issue.client_id,
        "jira_key": issue.jira_key,
        "jira_id": issue.jira_id,
        "project_key": issue.project_key,
        "summary": issue.summary,
        "description": issue.description,
        "issue_type": issue.issue_type,
        "status": issue.status,
        "priority": issue.priority,
        "assignee": issue.assignee,
        "reporter": issue.reporter,
        "labels": issue.labels,
        "jira_url": issue.jira_url,
        "created_jira": issue.created_jira.isoformat() if issue.created_jira else None,
        "updated_jira": issue.updated_jira.isoformat() if issue.updated_jira else None,
        "due_date": issue.due_date.isoformat() if issue.due_date else None,
        "synced_at": issue.synced_at.isoformat() if issue.synced_at else None,
    }


@router.get("/api/jira/settings")
async def api_jira_get_settings(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    creds = _get_jira_creds(user)
    token = creds.get("api_token", "")
    masked = ("*" * (len(token) - 4) + token[-4:]) if len(token) > 4 else ("*" * len(token))
    return {
        "url": creds.get("url", ""),
        "email": creds.get("email", ""),
        "api_token_masked": masked,
        "configured": bool(creds.get("url") and creds.get("email") and creds.get("api_token")),
    }


@router.post("/api/jira/settings")
async def api_jira_save_settings(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    data = await request.json()
    settings = dict(user.settings or {})
    jira = dict(settings.get("jira") or {})
    if data.get("url"):
        jira["url"] = data["url"].rstrip("/")
    if data.get("email"):
        jira["email"] = data["email"]
    if data.get("api_token"):
        try:
            from crypto import enc as _enc
            jira["api_token"] = _enc(data["api_token"])
        except Exception:
            jira["api_token"] = data["api_token"]
    settings["jira"] = jira
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}


@router.get("/api/jira/projects")
async def api_jira_projects(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    creds = _get_jira_creds(user)
    if not creds.get("url") or not creds.get("api_token"):
        raise HTTPException(status_code=400, detail="Jira not configured")
    try:
        from crypto import dec as _dec
        token = _dec(creds["api_token"])
    except Exception:
        token = creds["api_token"]
    import httpx
    async with httpx.AsyncClient(timeout=15) as hx:
        resp = await hx.get(
            f"{creds['url']}/rest/api/3/project/search",
            auth=(creds["email"], token),
            params={"maxResults": 100},
        )
    if resp.status_code != 200:
        return {"error": f"Jira returned HTTP {resp.status_code}"}
    data = resp.json()
    projects = [
        {"key": p["key"], "name": p["name"], "id": p["id"]}
        for p in data.get("values", [])
    ]
    return {"projects": projects}


@router.get("/api/jira/search")
async def api_jira_search(
    jql: Optional[str] = Query(None),
    client_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    creds = _get_jira_creds(user)
    if not creds.get("url") or not creds.get("api_token"):
        raise HTTPException(status_code=400, detail="Jira not configured")
    if not jql:
        if client_id:
            client = db.query(Client).filter(Client.id == client_id).first()
            jql = f'text ~ "{client.name}"' if client else "order by created DESC"
        else:
            jql = "order by created DESC"
    try:
        from crypto import dec as _dec
        token = _dec(creds["api_token"])
    except Exception:
        token = creds["api_token"]
    import httpx
    async with httpx.AsyncClient(timeout=20) as hx:
        resp = await hx.get(
            f"{creds['url']}/rest/api/3/search",
            auth=(creds["email"], token),
            params={"jql": jql, "maxResults": 50, "fields": "summary,status,priority,assignee,issuetype,created,updated,duedate,labels"},
        )
    if resp.status_code != 200:
        return {"error": f"Jira returned HTTP {resp.status_code}", "detail": resp.text[:300]}
    data = resp.json()
    issues = []
    for iss in data.get("issues", []):
        f = iss.get("fields", {})
        issues.append({
            "key": iss["key"],
            "id": iss["id"],
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "assignee": ((f.get("assignee") or {}).get("displayName")),
            "issue_type": (f.get("issuetype") or {}).get("name"),
            "created": f.get("created"),
            "updated": f.get("updated"),
            "due_date": f.get("duedate"),
            "labels": f.get("labels", []),
            "jira_url": f"{creds['url']}/browse/{iss['key']}",
        })
    return {"issues": issues, "total": data.get("total", len(issues))}


@router.get("/api/clients/{client_id}/jira")
async def api_client_jira_issues(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _require_user(auth_token, db)
    issues = (
        db.query(JiraIssue)
        .filter(JiraIssue.client_id == client_id)
        .order_by(JiraIssue.updated_jira.desc().nullslast(), JiraIssue.synced_at.desc())
        .all()
    )
    return {"issues": [_issue_dict(i) for i in issues]}


@router.post("/api/clients/{client_id}/jira/sync")
async def api_client_jira_sync(
    client_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)
    creds = _get_jira_creds(user)
    if not creds.get("url") or not creds.get("api_token"):
        raise HTTPException(status_code=400, detail="Jira not configured")
    try:
        from crypto import dec as _dec
        token = _dec(creds["api_token"])
    except Exception:
        token = creds["api_token"]

    try:
        body = await request.json()
    except Exception:
        body = {}
    jql = body.get("jql") or f'text ~ "{client.name}" order by updated DESC'

    import httpx
    async with httpx.AsyncClient(timeout=30) as hx:
        resp = await hx.get(
            f"{creds['url']}/rest/api/3/search",
            auth=(creds["email"], token),
            params={"jql": jql, "maxResults": 100, "fields": "summary,description,status,priority,assignee,reporter,issuetype,created,updated,duedate,labels,project"},
        )
    if resp.status_code != 200:
        return {"error": f"Jira returned HTTP {resp.status_code}"}

    data = resp.json()
    upserted = 0
    now = datetime.utcnow()
    for iss in data.get("issues", []):
        f = iss.get("fields", {})
        existing = db.query(JiraIssue).filter(JiraIssue.jira_key == iss["key"]).first()
        desc_raw = f.get("description")
        desc_text = None
        if isinstance(desc_raw, dict):
            try:
                desc_text = " ".join(
                    c.get("text", "") for block in desc_raw.get("content", [])
                    for c in block.get("content", []) if c.get("type") == "text"
                )
            except Exception:
                desc_text = str(desc_raw)
        elif isinstance(desc_raw, str):
            desc_text = desc_raw

        def _parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00").replace("+00:00", ""))
            except Exception:
                return None

        vals = dict(
            client_id=client_id,
            jira_id=iss.get("id"),
            project_key=(f.get("project") or {}).get("key"),
            summary=f.get("summary", ""),
            description=desc_text,
            issue_type=(f.get("issuetype") or {}).get("name"),
            status=(f.get("status") or {}).get("name"),
            priority=(f.get("priority") or {}).get("name"),
            assignee=(f.get("assignee") or {}).get("displayName"),
            reporter=(f.get("reporter") or {}).get("displayName"),
            labels=f.get("labels", []),
            jira_url=f"{creds['url']}/browse/{iss['key']}",
            created_jira=_parse_dt(f.get("created")),
            updated_jira=_parse_dt(f.get("updated")),
            due_date=_parse_dt(f.get("duedate")),
            synced_at=now,
        )
        if existing:
            for k, v in vals.items():
                setattr(existing, k, v)
        else:
            db.add(JiraIssue(jira_key=iss["key"], **vals))
        upserted += 1

    db.commit()
    return {"ok": True, "synced": upserted}


@router.post("/api/jira/issues")
async def api_jira_create_issue(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    creds = _get_jira_creds(user)
    if not creds.get("url") or not creds.get("api_token"):
        raise HTTPException(status_code=400, detail="Jira not configured")
    try:
        from crypto import dec as _dec
        token = _dec(creds["api_token"])
    except Exception:
        token = creds["api_token"]
    data = await request.json()
    payload = {
        "fields": {
            "project": {"key": data["project_key"]},
            "summary": data["summary"],
            "issuetype": {"name": data.get("issue_type", "Task")},
        }
    }
    if data.get("description"):
        payload["fields"]["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": data["description"]}]}],
        }
    import httpx
    async with httpx.AsyncClient(timeout=15) as hx:
        resp = await hx.post(
            f"{creds['url']}/rest/api/3/issue",
            auth=(creds["email"], token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        return {"error": f"Jira returned HTTP {resp.status_code}", "detail": resp.text[:300]}
    result = resp.json()
    jira_key = result.get("key")
    if data.get("client_id") and jira_key:
        new_issue = JiraIssue(
            client_id=data["client_id"],
            jira_key=jira_key,
            jira_id=result.get("id"),
            project_key=data["project_key"],
            summary=data["summary"],
            description=data.get("description"),
            issue_type=data.get("issue_type", "Task"),
            status="Open",
            jira_url=f"{creds['url']}/browse/{jira_key}",
            synced_at=datetime.utcnow(),
        )
        db.add(new_issue)
        db.commit()
    return {"ok": True, "key": jira_key, "id": result.get("id")}


@router.post("/api/jira/issues/{issue_key}/comment")
async def api_jira_add_comment(
    issue_key: str,
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    user = _require_user(auth_token, db)
    creds = _get_jira_creds(user)
    if not creds.get("url") or not creds.get("api_token"):
        raise HTTPException(status_code=400, detail="Jira not configured")
    try:
        from crypto import dec as _dec
        token = _dec(creds["api_token"])
    except Exception:
        token = creds["api_token"]
    data = await request.json()
    body_text = data.get("body") or data.get("comment") or ""
    comment_payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": body_text}]}],
        }
    }
    import httpx
    async with httpx.AsyncClient(timeout=15) as hx:
        resp = await hx.post(
            f"{creds['url']}/rest/api/3/issue/{issue_key}/comment",
            auth=(creds["email"], token),
            json=comment_payload,
        )
    if resp.status_code not in (200, 201):
        return {"error": f"Jira returned HTTP {resp.status_code}", "detail": resp.text[:300]}
    return {"ok": True, "id": resp.json().get("id")}
