"""Background Jira comment poller + Time notification sender."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_POLL_TASK: asyncio.Task | None = None


# ── Time message sender ───────────────────────────────────────────────────────

def send_time_post(token: str, channel_id: str, message: str) -> None:
    """Send a message to a Time channel using Bearer token."""
    with httpx.Client(timeout=30.0) as http:
        r = http.post(
            f"{settings.time_api_base}/channels/{channel_id}/posts",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"message": message},
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Time API {r.status_code}: {r.text[:300]}")


def resolve_channel_id_from_url(token: str, channel_url: str) -> str:
    """Parse team/channel from URL and resolve to channel_id via Time API."""
    from backend.url_parse import parse_channel_url

    parsed = parse_channel_url(channel_url)
    if not parsed:
        raise ValueError(f"Не удалось распознать URL канала: {channel_url}")
    from backend.time_client import TimeClient

    client = TimeClient(token)
    team = client.get_team_by_name(parsed.team_name)
    ch = client.get_channel_by_name(team["id"], parsed.channel_name)
    return str(ch.get("id") or "")


def format_comment_notification(
    issue_key: str,
    summary: str,
    author: str,
    preview: str,
    browse_url: str,
    watcher_label: str = "",
) -> str:
    parts = []
    if watcher_label:
        parts.append(f"[{watcher_label}]")
    parts.append(f"**{author}** прокомментировал {issue_key}")
    msg = " ".join(parts)
    lines = [msg]
    if summary:
        lines.append(summary)
    if preview:
        short = preview[:250] + ("…" if len(preview) > 250 else "")
        lines.append(f"> {short}")
    if browse_url:
        lines.append(browse_url)
    return "\n".join(lines)


# ── Poll logic (sync, runs in thread) ─────────────────────────────────────────

def _do_poll_sync() -> None:
    """One poll iteration. Runs blocking I/O — call via asyncio.to_thread."""
    from backend.accounts import board_db_path, resolve_active_board_account_id
    from backend.auth_tokens import get_access_token_for_api
    from backend.database import (
        JiraNotifyState,
        JiraWatcher,
        NotificationSettings,
        get_sessionmaker,
    )
    from backend import jira_client, jira_write

    aid = resolve_active_board_account_id()
    if not aid:
        return

    db = get_sessionmaker(board_db_path(aid))()
    try:
        ns: NotificationSettings | None = db.query(NotificationSettings).first()
        if not ns or not ns.enabled or not ns.time_channel_id or not ns.notify_new_comments:
            return

        watchers: list[JiraWatcher] = (
            db.query(JiraWatcher).filter(JiraWatcher.enabled == True).all()
        )
        if not watchers:
            return

        if not jira_client.jira_configured():
            return

        try:
            token = get_access_token_for_api(db)
        except PermissionError:
            return

        try:
            me = jira_client.get_myself()
        except Exception as exc:
            logger.warning("Jira /myself failed during poll: %s", exc)
            return

        my_name = (me.get("name") or me.get("key") or "").strip().lower()
        my_account_id = (me.get("accountId") or "").strip()

        for watcher in watchers:
            _poll_watcher(db, ns, watcher, token, my_name, my_account_id)

        db.commit()
    except Exception:
        logger.exception("Poll iteration error")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _poll_watcher(
    db: Any,
    ns: Any,
    watcher: Any,
    token: str,
    my_name: str,
    my_account_id: str,
) -> None:
    from backend import jira_client, jira_write
    from backend.database import JiraNotifyState

    try:
        result = jira_client.search_issues(watcher.jql, max_fetch=200)
        issues: list[dict[str, Any]] = result.get("issues") or []
    except Exception as exc:
        logger.warning("Watcher %s JQL error: %s", watcher.id, exc)
        return

    for issue in issues:
        key: str = issue.get("key") or ""
        if not key:
            continue
        try:
            _poll_issue(db, ns, watcher, token, my_name, my_account_id, issue)
        except Exception as exc:
            logger.warning("Error processing issue %s in watcher %s: %s", key, watcher.id, exc)


def _poll_issue(
    db: Any,
    ns: Any,
    watcher: Any,
    token: str,
    my_name: str,
    my_account_id: str,
    issue: dict[str, Any],
) -> None:
    from backend import jira_write
    from backend.database import JiraNotifyState

    key: str = issue.get("key") or ""
    state: JiraNotifyState | None = (
        db.query(JiraNotifyState)
        .filter(JiraNotifyState.issue_key == key, JiraNotifyState.watcher_id == watcher.id)
        .first()
    )

    if state is None:
        # First time — record current state without notifying
        existing = issue.get("last_comment_created") or ""
        try:
            all_comments = jira_write.get_issue_comments(key)
            if all_comments:
                existing = max(str(c.get("created") or "") for c in all_comments)
        except Exception:
            pass
        db.add(JiraNotifyState(issue_key=key, watcher_id=watcher.id, last_seen_comment_created=existing))
        return

    # Fetch new comments since last seen
    since = state.last_seen_comment_created or ""
    try:
        new_comments = jira_write.get_issue_comments(key, since_iso=since)
    except Exception as exc:
        logger.warning("Failed to get comments for %s: %s", key, exc)
        return

    if not new_comments:
        return

    new_latest = max(str(c.get("created") or "") for c in new_comments)

    for comment in new_comments:
        author = comment.get("author") or {}
        author_name_lc = (author.get("name") or author.get("key") or "").strip().lower()
        author_account_id = (author.get("accountId") or "").strip()
        author_display = (author.get("displayName") or author.get("name") or "?").strip()

        # Skip my own comments
        if my_name and author_name_lc == my_name:
            continue
        if my_account_id and author_account_id == my_account_id:
            continue

        preview = jira_write.comment_preview(comment)
        msg = format_comment_notification(
            issue_key=key,
            summary=issue.get("summary") or "",
            author=author_display,
            preview=preview,
            browse_url=issue.get("browse_url") or "",
            watcher_label=watcher.label or "",
        )
        try:
            send_time_post(token, ns.time_channel_id, msg)
        except Exception as exc:
            logger.warning("Failed to send Time notification for %s: %s", key, exc)

    if new_latest > since:
        state.last_seen_comment_created = new_latest
        db.add(state)


def _get_poll_interval_sync() -> int:
    try:
        from backend.accounts import board_db_path, resolve_active_board_account_id
        from backend.database import NotificationSettings, get_sessionmaker

        aid = resolve_active_board_account_id()
        if not aid:
            return 180
        db = get_sessionmaker(board_db_path(aid))()
        try:
            ns = db.query(NotificationSettings).first()
            if ns:
                return max(60, ns.poll_interval_sec or 180)
        finally:
            db.close()
    except Exception:
        pass
    return 180


# ── Async wrapper ─────────────────────────────────────────────────────────────

async def _poll_loop() -> None:
    # Initial delay so the server finishes startup before first poll
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(_do_poll_sync)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Notification poll loop error")
        interval = await asyncio.to_thread(_get_poll_interval_sync)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


def start_poll_task() -> None:
    global _POLL_TASK
    if _POLL_TASK is None or _POLL_TASK.done():
        _POLL_TASK = asyncio.ensure_future(_poll_loop())
        logger.info("Jira notification poll task started")


def stop_poll_task() -> None:
    global _POLL_TASK
    if _POLL_TASK and not _POLL_TASK.done():
        _POLL_TASK.cancel()
        logger.info("Jira notification poll task stopped")
