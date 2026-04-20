from __future__ import annotations

from typing import Any

import httpx

from backend.config import settings
from backend.jira_client import JiraApiError, jira_configured
from backend.jira_text import adf_to_plain


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.jira_token.strip()}",
        "Accept": "application/json",
    }


def _json_headers() -> dict[str, str]:
    return {**_auth_headers(), "Content-Type": "application/json"}


def _base() -> str:
    return settings.jira_base_url.rstrip("/")


def _check() -> None:
    if not jira_configured():
        raise JiraApiError(503, "Jira не настроена (JIRA_BASE_URL / JIRA_TOKEN)")


def _raise_for(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            body = r.json()
            msgs = body.get("errorMessages") or []
            errs = body.get("errors") or {}
            detail = "; ".join(msgs) or "; ".join(f"{k}: {v}" for k, v in errs.items())
        except Exception:
            detail = r.text[:400]
        raise JiraApiError(r.status_code, detail or r.text[:400])


# ── Transitions ──────────────────────────────────────────────────────────────

def get_transitions(issue_key: str) -> list[dict[str, Any]]:
    _check()
    with httpx.Client(timeout=30.0) as http:
        r = http.get(
            f"{_base()}/rest/api/2/issue/{issue_key}/transitions",
            headers=_auth_headers(),
        )
    _raise_for(r)
    return [
        {"id": str(t.get("id") or ""), "name": str(t.get("name") or "")}
        for t in (r.json().get("transitions") or [])
        if isinstance(t, dict) and t.get("id")
    ]


def transition_issue(issue_key: str, transition_id: str) -> None:
    _check()
    with httpx.Client(timeout=30.0) as http:
        r = http.post(
            f"{_base()}/rest/api/2/issue/{issue_key}/transitions",
            headers=_json_headers(),
            json={"transition": {"id": transition_id}},
        )
    _raise_for(r)


# ── Assign ────────────────────────────────────────────────────────────────────

def assign_issue(issue_key: str, assignee_name: str | None) -> None:
    """Assign to user (Jira Server/DC — uses 'name' field; null = unassign)."""
    _check()
    payload: dict[str, Any] = {"name": assignee_name} if assignee_name else {"name": None}
    with httpx.Client(timeout=30.0) as http:
        r = http.put(
            f"{_base()}/rest/api/2/issue/{issue_key}/assignee",
            headers=_json_headers(),
            json=payload,
        )
    _raise_for(r)


def search_users(query: str, max_results: int = 15) -> list[dict[str, Any]]:
    """Search Jira users for assignee autocomplete (Jira Server/DC)."""
    _check()
    with httpx.Client(timeout=20.0) as http:
        r = http.get(
            f"{_base()}/rest/api/2/user/search",
            params={"username": query, "maxResults": max_results},
            headers=_auth_headers(),
        )
    _raise_for(r)
    users = r.json()
    if not isinstance(users, list):
        return []
    return [
        {
            "name": str(u.get("name") or u.get("key") or ""),
            "displayName": str(u.get("displayName") or ""),
            "accountId": str(u.get("accountId") or ""),
        }
        for u in users
        if isinstance(u, dict) and (u.get("name") or u.get("key"))
    ]


# ── Comments ──────────────────────────────────────────────────────────────────

def add_comment(issue_key: str, text: str) -> dict[str, Any]:
    """Add plain-text comment to Jira issue."""
    _check()
    with httpx.Client(timeout=30.0) as http:
        r = http.post(
            f"{_base()}/rest/api/2/issue/{issue_key}/comment",
            headers=_json_headers(),
            json={"body": text},
        )
    _raise_for(r)
    return r.json()


def get_issue_comments(issue_key: str, since_iso: str | None = None) -> list[dict[str, Any]]:
    """Return all comments for an issue, optionally only those after since_iso."""
    _check()
    with httpx.Client(timeout=30.0) as http:
        r = http.get(
            f"{_base()}/rest/api/2/issue/{issue_key}/comment",
            params={"maxResults": 100, "orderBy": "created"},
            headers=_auth_headers(),
        )
    _raise_for(r)
    comments: list[dict[str, Any]] = r.json().get("comments") or []
    if since_iso:
        comments = [c for c in comments if str(c.get("created") or "") > since_iso]
    return comments


# ── Attachments ───────────────────────────────────────────────────────────────

def add_attachment(
    issue_key: str, filename: str, content: bytes, content_type: str
) -> list[dict[str, Any]]:
    """Attach a file to a Jira issue. Returns list of attachment objects."""
    _check()
    headers = {
        "Authorization": f"Bearer {settings.jira_token.strip()}",
        "X-Atlassian-Token": "no-check",
    }
    with httpx.Client(timeout=60.0) as http:
        r = http.post(
            f"{_base()}/rest/api/2/issue/{issue_key}/attachments",
            headers=headers,
            files={"file": (filename, content, content_type)},
        )
    _raise_for(r)
    result = r.json()
    return result if isinstance(result, list) else [result]


# ── Helpers for notification poller ──────────────────────────────────────────

def comment_preview(comment: dict[str, Any], limit: int = 300) -> str:
    body = comment.get("body") or ""
    if isinstance(body, dict):
        text = adf_to_plain(body)
    else:
        text = str(body)
    text = " ".join(text.split())
    return (text[:limit] + "…") if len(text) > limit else text
