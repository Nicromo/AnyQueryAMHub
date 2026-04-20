from __future__ import annotations

from typing import Any
import httpx

from backend.config import settings
from backend.jira_text import adf_to_plain, description_plain


class JiraApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def project_key_from_issue_key(key: str) -> str:
    k = (key or "").strip().upper()
    if not k:
        return ""
    i = k.rfind("-")
    if i <= 0:
        return k
    return k[:i]


def _comment_body_preview(body: Any, limit: int = 280) -> str:
    if isinstance(body, str):
        s = body.replace("\r\n", "\n").replace("\r", "\n")
    elif isinstance(body, dict):
        s = adf_to_plain(body)
    else:
        s = str(body or "")
    s = " ".join(s.split())
    return (s[:limit] + "…") if len(s) > limit else s


def _latest_comment_meta(comment_block: Any) -> dict[str, str]:
    """Поля последнего комментария: автор (отображение, accountId, name), превью, дата."""
    blank = {
        "author_display": "",
        "author_account_id": "",
        "author_name": "",
        "preview": "",
        "created": "",
    }

    def empty() -> dict[str, str]:
        return dict(blank)

    if not isinstance(comment_block, dict):
        return empty()
    comments = comment_block.get("comments") or []
    if not isinstance(comments, list) or not comments:
        return empty()
    latest: dict[str, Any] | None = None
    latest_created = ""
    for c in comments:
        if not isinstance(c, dict):
            continue
        cr = str(c.get("created") or "")
        if latest is None or cr > latest_created:
            latest = c
            latest_created = cr
    if not latest:
        return empty()
    auth = latest.get("author") or {}
    author_display = ""
    author_account_id = ""
    author_name = ""
    if isinstance(auth, dict):
        author_display = str(auth.get("displayName") or auth.get("name") or "").strip()
        author_account_id = str(auth.get("accountId") or "").strip()
        author_name = str(auth.get("name") or auth.get("key") or "").strip()
    preview = _comment_body_preview(latest.get("body"))
    return {
        "author_display": author_display,
        "author_account_id": author_account_id,
        "author_name": author_name,
        "preview": preview,
        "created": latest_created,
    }


def normalize_issue(issue: dict[str, Any], browse_base: str) -> dict[str, Any]:
    key = str(issue.get("key") or "")
    fields = issue.get("fields") or {}
    st = fields.get("status") or {}
    status_name = str(st.get("name") or "")
    pr = fields.get("priority") or {}
    priority_name = str(pr.get("name") or "")
    base = browse_base.rstrip("/")
    desc = description_plain(fields.get("description"))
    cm = _latest_comment_meta(fields.get("comment"))
    updated = str(fields.get("updated") or "")
    sort_ts = cm["created"] or updated
    return {
        "key": key,
        "project_key": project_key_from_issue_key(key),
        "summary": str(fields.get("summary") or ""),
        "description": desc,
        "status": status_name,
        "priority": priority_name,
        "updated": updated,
        "browse_url": f"{base}/browse/{key}" if key else base,
        "last_comment_author": cm["author_display"],
        "last_comment_author_account_id": cm["author_account_id"],
        "last_comment_author_name": cm["author_name"],
        "last_comment_preview": cm["preview"],
        "last_comment_created": cm["created"],
        "sort_timestamp": sort_ts,
    }


def jira_configured() -> bool:
    return bool((settings.jira_base_url or "").strip() and (settings.jira_token or "").strip())


def get_myself() -> dict[str, Any]:
    if not jira_configured():
        raise JiraApiError(503, "Jira не настроена (JIRA_BASE_URL / JIRA_TOKEN)")
    base = settings.jira_base_url.rstrip("/")
    with httpx.Client(timeout=45.0, verify=True) as http:
        r = http.get(
            f"{base}/rest/api/2/myself",
            headers={
                "Authorization": f"Bearer {settings.jira_token.strip()}",
                "Accept": "application/json",
            },
        )
    if r.status_code == 401:
        raise JiraApiError(401, "Jira: 401 — проверьте JIRA_TOKEN")
    if r.status_code == 403:
        raise JiraApiError(403, "Jira: 403 — нет прав на REST API")
    if r.status_code >= 400:
        raise JiraApiError(r.status_code, r.text[:500] if r.text else str(r.status_code))
    return r.json()


def _search_page(
    http: httpx.Client,
    base: str,
    headers: dict[str, str],
    jql: str,
    start_at: int,
    page_size: int,
    fields: str,
) -> httpx.Response:
    params = {
        "jql": jql,
        "startAt": start_at,
        "maxResults": max(1, min(page_size, 100)),
        "fields": fields,
    }
    return http.get(f"{base}/rest/api/2/search", params=params, headers=headers)


def search_issues(
    jql: str,
    *,
    start_at: int = 0,
    max_results: int = 50,
    max_fetch: int | None = None,
) -> dict[str, Any]:
    """
    Загрузка задач из Jira. Одна страница API — до 100 шт.

    max_fetch: если задан — ходим по страницам, пока не наберём столько задач
    (или кончится выборка / лимит Jira). Иначе — ровно одна страница размером max_results.
    """
    if not jira_configured():
        raise JiraApiError(503, "Jira не настроена (JIRA_BASE_URL / JIRA_TOKEN)")
    base = settings.jira_base_url.rstrip("/")
    field_sets = (
        "key,summary,status,updated,priority,description,comment",
        "key,summary,status,updated,priority,description",
        "key,summary,status,updated,priority",
    )
    headers = {
        "Authorization": f"Bearer {settings.jira_token.strip()}",
        "Accept": "application/json",
    }
    page_size = max(1, min(max_results, 100))
    want_total = max_fetch if max_fetch is not None else page_size
    want_total = max(1, min(want_total, 2000))

    with httpx.Client(timeout=90.0, verify=True) as http:
        fields_ok: str | None = None
        r: httpx.Response | None = None
        for fields in field_sets:
            r = _search_page(http, base, headers, jql, start_at, page_size, fields)
            if r.status_code == 400 and fields != field_sets[-1]:
                continue
            fields_ok = fields
            break
        assert r is not None and fields_ok is not None

        if r.status_code == 401:
            raise JiraApiError(401, "Jira: 401 — проверьте JIRA_TOKEN")
        if r.status_code == 403:
            raise JiraApiError(403, "Jira: 403 — нет прав на поиск (JQL)")
        if r.status_code == 400:
            try:
                body = r.json()
                msgs = body.get("errorMessages") or []
                em = "; ".join(msgs) if msgs else r.text[:400]
            except Exception:
                em = r.text[:400]
            raise JiraApiError(400, f"Jira JQL / запрос: {em}")
        if r.status_code >= 400:
            raise JiraApiError(r.status_code, r.text[:500] if r.text else str(r.status_code))

        data = r.json()
        total = int(data.get("total") or 0)
        issues_raw = data.get("issues") or []
        all_issues: list[dict[str, Any]] = [
            normalize_issue(i, base) for i in issues_raw if isinstance(i, dict)
        ]

        if max_fetch is None:
            return {
                "issues": all_issues,
                "total": total,
                "start_at": int(data.get("startAt") or start_at),
                "max_results": int(data.get("maxResults") or page_size),
                "loaded_count": len(all_issues),
                "jql_used": jql,
            }

        remaining = min(want_total, max(0, total - start_at)) - len(all_issues)
        cursor = start_at + len(all_issues)

        while remaining > 0 and len(all_issues) < want_total:
            take = min(page_size, remaining, want_total - len(all_issues))
            if take <= 0:
                break
            r2 = _search_page(http, base, headers, jql, cursor, take, fields_ok)
            if r2.status_code >= 400:
                break
            data2 = r2.json()
            chunk_raw = data2.get("issues") or []
            chunk = [normalize_issue(i, base) for i in chunk_raw if isinstance(i, dict)]
            if not chunk:
                break
            all_issues.extend(chunk)
            cursor += len(chunk)
            remaining = min(want_total, max(0, total - start_at)) - len(all_issues)

    return {
        "issues": all_issues,
        "total": total,
        "start_at": start_at,
        "max_results": page_size,
        "loaded_count": len(all_issues),
        "jql_used": jql,
    }
