"""
Jira REST API v3 Integration
Управление задачами и проектами через Jira Cloud REST API

Конфигурация через переменные окружения (глобальные fallback):
  JIRA_BASE_URL  - https://yourcompany.atlassian.net
  JIRA_EMAIL     - email для Basic Auth
  JIRA_API_TOKEN - API token (atlassian.com → Account settings → Security)

Пользовательские настройки берутся из user.settings["jira"]:
  {"url": str, "email": str, "api_token": str}
"""

import os
import logging
from base64 import b64encode
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

_API_PATH = "/rest/api/3"


# ── Auth / client helpers ─────────────────────────────────────────────────────

def _resolve(settings: Dict[str, Any], key: str, env_fallback: str) -> str:
    """Берём значение из user.settings["jira"], fallback — env."""
    jira = settings.get("jira", {}) if isinstance(settings, dict) else {}
    return jira.get(key) or env_fallback


def _base_url(settings: Dict[str, Any]) -> str:
    return _resolve(settings, "url", JIRA_BASE_URL).rstrip("/")


def _headers(settings: Dict[str, Any]) -> Dict[str, str]:
    email = _resolve(settings, "email", JIRA_EMAIL)
    token = _resolve(settings, "api_token", JIRA_API_TOKEN)
    credentials = b64encode(f"{email}:{token}".encode()).decode("ascii")
    return {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _is_configured(settings: Dict[str, Any]) -> bool:
    return bool(_base_url(settings) and _resolve(settings, "email", JIRA_EMAIL) and _resolve(settings, "api_token", JIRA_API_TOKEN))


def _api(settings: Dict[str, Any], path: str) -> str:
    return f"{_base_url(settings)}{_API_PATH}{path}"


# ── Projects ──────────────────────────────────────────────────────────────────

async def get_projects(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Получить список доступных проектов.

    Returns:
        [{"id", "key", "name", "projectTypeKey", "style"}]
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _api(settings, "/project/search"),
                headers=_headers(settings),
                params={"maxResults": 100, "expand": "description"},
            )

        if resp.status_code != 200:
            logger.warning(f"Jira get_projects error: {resp.status_code} {resp.text[:200]}")
            return []

        values = resp.json().get("values", [])
        return [
            {
                "id": p.get("id"),
                "key": p.get("key"),
                "name": p.get("name"),
                "projectTypeKey": p.get("projectTypeKey"),
                "style": p.get("style"),
            }
            for p in values
        ]

    except Exception as e:
        logger.error(f"Jira get_projects error: {e}")
        return []


# ── Issues / search ───────────────────────────────────────────────────────────

async def search_issues(
    settings: Dict[str, Any],
    jql: str,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """
    Поиск задач по JQL.

    Returns:
        [{"id", "key", "summary", "status", "priority", "assignee",
          "reporter", "created", "updated", "labels", "components"}]
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return []

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                _api(settings, "/issue/search"),
                headers=_headers(settings),
                json={
                    "jql": jql,
                    "maxResults": max_results,
                    "fields": [
                        "summary", "status", "priority", "assignee",
                        "reporter", "created", "updated", "labels",
                        "components", "issuetype", "description",
                    ],
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Jira search_issues error: {resp.status_code} {resp.text[:200]}")
            return []

        return [_normalize_issue(i) for i in resp.json().get("issues", [])]

    except Exception as e:
        logger.error(f"Jira search_issues error: {e}")
        return []


async def get_issue(settings: Dict[str, Any], issue_key: str) -> Optional[Dict[str, Any]]:
    """
    Получить детали задачи по ключу (например PROJECT-123).

    Returns:
        Нормализованный словарь задачи или None.
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _api(settings, f"/issue/{issue_key}"),
                headers=_headers(settings),
                params={
                    "fields": "summary,status,priority,assignee,reporter,created,updated,labels,components,issuetype,description,comment"
                },
            )

        if resp.status_code == 404:
            logger.info(f"Jira issue {issue_key} not found")
            return None

        if resp.status_code != 200:
            logger.warning(f"Jira get_issue error: {resp.status_code} {resp.text[:200]}")
            return None

        return _normalize_issue(resp.json())

    except Exception as e:
        logger.error(f"Jira get_issue error: {e}")
        return None


async def get_issues_for_project(
    settings: Dict[str, Any],
    project_key: str,
    client_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Получить задачи проекта, опционально отфильтрованные по метке или компоненту,
    совпадающему с именем клиента.

    Args:
        settings:     Настройки пользователя
        project_key:  Ключ проекта Jira
        client_name:  Имя клиента для фильтрации по label/component (optional)
    """
    jql = f'project = "{project_key}" ORDER BY updated DESC'

    if client_name:
        safe = client_name.replace('"', '\\"')
        jql = (
            f'project = "{project_key}" AND '
            f'(labels = "{safe}" OR component = "{safe}") '
            f'ORDER BY updated DESC'
        )

    return await search_issues(settings, jql, max_results=100)


# ── Mutations ─────────────────────────────────────────────────────────────────

async def create_issue(
    settings: Dict[str, Any],
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    priority: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Создать задачу в Jira.

    Returns:
        {"id", "key", "url"} или None.
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return None

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "description": _adf_doc(description),
        "issuetype": {"name": issue_type},
    }
    if priority:
        fields["priority"] = {"name": priority}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _api(settings, "/issue"),
                headers=_headers(settings),
                json={"fields": fields},
            )

        if resp.status_code not in (200, 201):
            logger.warning(f"Jira create_issue error: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        issue_key = data.get("key", "")
        return {
            "id": data.get("id"),
            "key": issue_key,
            "url": f"{_base_url(settings)}/browse/{issue_key}",
        }

    except Exception as e:
        logger.error(f"Jira create_issue error: {e}")
        return None


async def update_issue(
    settings: Dict[str, Any],
    issue_key: str,
    fields: Dict[str, Any],
) -> bool:
    """
    Обновить поля задачи.

    Args:
        settings:   Настройки пользователя
        issue_key:  Ключ задачи (PROJECT-123)
        fields:     Словарь полей для обновления (формат Jira API)

    Returns:
        True при успехе.
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                _api(settings, f"/issue/{issue_key}"),
                headers=_headers(settings),
                json={"fields": fields},
            )

        if resp.status_code == 204:
            logger.info(f"Jira issue {issue_key} updated")
            return True

        logger.warning(f"Jira update_issue error: {resp.status_code} {resp.text[:200]}")
        return False

    except Exception as e:
        logger.error(f"Jira update_issue error: {e}")
        return False


async def add_comment(
    settings: Dict[str, Any],
    issue_key: str,
    comment: str,
) -> Optional[Dict[str, Any]]:
    """
    Добавить комментарий к задаче.

    Returns:
        {"id", "body", "author", "created"} или None.
    """
    if not _is_configured(settings):
        logger.warning("Jira not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _api(settings, f"/issue/{issue_key}/comment"),
                headers=_headers(settings),
                json={"body": _adf_doc(comment)},
            )

        if resp.status_code not in (200, 201):
            logger.warning(f"Jira add_comment error: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        return {
            "id": data.get("id"),
            "body": comment,
            "author": data.get("author", {}).get("displayName", ""),
            "created": data.get("created", ""),
        }

    except Exception as e:
        logger.error(f"Jira add_comment error: {e}")
        return None


# ── Normalization helpers ─────────────────────────────────────────────────────

def _normalize_issue(raw: Dict[str, Any]) -> Dict[str, Any]:
    fields = raw.get("fields", {})
    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    priority = fields.get("priority") or {}
    status = fields.get("status") or {}
    issue_type = fields.get("issuetype") or {}

    return {
        "id": raw.get("id"),
        "key": raw.get("key"),
        "summary": fields.get("summary", ""),
        "status": status.get("name", ""),
        "priority": priority.get("name", ""),
        "issue_type": issue_type.get("name", ""),
        "assignee": assignee.get("displayName", ""),
        "reporter": reporter.get("displayName", ""),
        "created": fields.get("created", ""),
        "updated": fields.get("updated", ""),
        "labels": fields.get("labels", []),
        "components": [c.get("name", "") for c in fields.get("components", [])],
        "description": _extract_adf_text(fields.get("description")),
    }


def _adf_doc(text: str) -> Dict[str, Any]:
    """Оборачиваем plain text в формат Atlassian Document Format (ADF)."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _extract_adf_text(doc: Optional[Dict[str, Any]]) -> str:
    """Извлечь plain text из ADF-документа (рекурсивно)."""
    if not doc:
        return ""
    if isinstance(doc, str):
        return doc

    parts = []
    if doc.get("type") == "text":
        parts.append(doc.get("text", ""))
    for child in doc.get("content", []):
        parts.append(_extract_adf_text(child))
    return " ".join(p for p in parts if p).strip()
