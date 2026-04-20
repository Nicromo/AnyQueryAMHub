from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.column_channels import parse_targets_json, resolve_all_channel_ids
from backend.database import Case, IgnoredThread, KanbanColumn, SyncCursor, utcnow
from backend.parsers import build_parsed_fields, post_matches_column_rules
from backend.time_client import (
    TimeClient,
    extract_mention_user_ids,
    post_permalink,
    root_post_id_from,
)
from backend.url_parse import parse_post_permalink


def _ignored(db: Session, channel_id: str, root_id: str) -> bool:
    return (
        db.query(IgnoredThread)
        .filter(
            IgnoredThread.channel_id == channel_id,
            IgnoredThread.root_post_id == root_id,
        )
        .first()
        is not None
    )


def _get_or_create_cursor(db: Session, channel_id: str) -> SyncCursor:
    row = db.query(SyncCursor).filter(SyncCursor.channel_id == channel_id).first()
    if row:
        return row
    row = SyncCursor(channel_id=channel_id, since_ms=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _ingest_bundle_into_cases(
    db: Session,
    col: KanbanColumn,
    channel_id: str,
    client: TimeClient,
    my_id: str,
    my_username: str,
    user_cache: dict[str, str],
    rules: dict[str, Any],
    order: list[str],
    posts: dict[str, Any],
    since_floor: int,
    seen_roots: set[str] | None = None,
) -> tuple[int, int]:
    """Обрабатывает один ответ API posts. Возвращает (число новых кейсов, max create_at в батче)."""
    if seen_roots is None:
        seen_roots = set()
    max_seen = since_floor
    created = 0
    for post_id in reversed(order):
        post = posts.get(post_id)
        if not post:
            continue
        ca = int(post.get("create_at") or 0)
        if ca > max_seen:
            max_seen = ca

        root_id = root_post_id_from(post)
        if not root_id:
            continue
        if _ignored(db, channel_id, root_id):
            continue

        if root_id != post.get("id"):
            case = (
                db.query(Case)
                .filter(Case.channel_id == channel_id, Case.root_post_id == root_id)
                .first()
            )
            if case and case.status == "active":
                uid = post.get("user_id") or ""
                msg = (post.get("message") or "").replace("\n", " ").strip()[:500]
                if uid and uid not in user_cache:
                    try:
                        u = client.get_user(uid)
                        user_cache[uid] = u.get("username") or u.get("nickname") or uid
                    except Exception:
                        user_cache[uid] = uid
                if ca >= case.last_activity_at_ms:
                    case.last_activity_at_ms = ca
                    case.last_message_user_id = uid
                    case.last_message_username = user_cache.get(uid, "")
                    case.last_message_preview = msg
                    case.updated_at = utcnow()
                    db.add(case)
            continue

        root = post
        message = root.get("message") or ""
        is_self = root.get("user_id") == my_id
        # If channel is in "own posts" mode — skip posts authored by others
        if rules.get("match_self_only") and not is_self:
            continue
        mentions = extract_mention_user_ids(root)
        if not post_matches_column_rules(message, rules, my_username, my_id, mentions):
            continue

        if root_id in seen_roots:
            continue

        existing = (
            db.query(Case)
            .filter(Case.channel_id == channel_id, Case.root_post_id == root_id)
            .first()
        )
        parsed = build_parsed_fields(message, rules)
        initiator = "self" if is_self else "unspecified"
        permalink = post_permalink(col.team_name, root_id)
        site_id = str(parsed.get("site_id")) if parsed.get("site_id") else None
        title = (parsed.get("description") or message)[:1024]
        assignee = parsed.get("assignee_raw")

        if existing:
            # Один канал в нескольких колонках: кейс один на (channel_id, root); показываем в той колонке, откуда последний синк
            if existing.column_id != col.id:
                existing.column_id = col.id
            existing.raw_root_message = message
            ru = str(root.get("user_id") or "")
            if ru:
                existing.root_author_user_id = ru
            existing.site_id = site_id or existing.site_id
            existing.assignee_raw = assignee or existing.assignee_raw
            existing.title_preview = title or existing.title_preview
            existing.permalink = permalink or existing.permalink
            existing.updated_at = utcnow()
            db.add(existing)
            continue

        rauth = str(root.get("user_id") or "")
        case = Case(
            column_id=col.id,
            root_post_id=root_id,
            channel_id=channel_id,
            team_name=col.team_name,
            status="active",
            initiator=initiator,
            root_author_user_id=rauth,
            site_id=site_id,
            assignee_raw=assignee,
            title_preview=title,
            permalink=permalink,
            raw_root_message=message,
            last_activity_at_ms=ca,
            last_message_user_id=rauth,
            last_message_preview=(message.replace("\n", " ").strip()[:500]),
            manual=False,
        )
        uid0 = case.last_message_user_id
        if uid0 and uid0 not in user_cache:
            try:
                u = client.get_user(uid0)
                user_cache[uid0] = u.get("username") or u.get("nickname") or uid0
            except Exception:
                user_cache[uid0] = uid0
        case.last_message_username = user_cache.get(uid0, "")
        db.add(case)
        seen_roots.add(root_id)
        created += 1

    return created, max_seen


def refresh_case_from_thread(
    db: Session,
    case: Case,
    client: TimeClient,
    user_cache: dict[str, str],
) -> None:
    data = client.get_post_thread(case.root_post_id)
    posts: dict[str, Any] = data.get("posts") or {}
    order: list[str] = data.get("order") or []
    if not posts or not order:
        return
    root = posts.get(case.root_post_id)
    if root and root.get("message"):
        case.raw_root_message = str(root.get("message") or "")
        ru = str(root.get("user_id") or "")
        if ru:
            case.root_author_user_id = ru
        col = db.query(KanbanColumn).filter(KanbanColumn.id == case.column_id).first()
        if col:
            parsed = build_parsed_fields(case.raw_root_message, col.rules())
            sid = parsed.get("site_id")
            if sid:
                case.site_id = str(sid)
    # Find last non-workflow message for display (iterate from newest to oldest)
    last_id = order[0]
    last = posts.get(last_id) or {}
    create_at = int(last.get("create_at") or 0)
    uid = last.get("user_id") or ""
    msg = (last.get("message") or "").replace("\n", " ").strip()[:500]
    
    # Look for last non-workflow user
    last_non_workflow_uid = None
    last_non_workflow_msg = None
    for pid in order:
        p = posts.get(pid)
        if not p:
            continue
        check_uid = p.get("user_id") or ""
        if not check_uid:
            continue
        if check_uid not in user_cache:
            try:
                u = client.get_user(check_uid)
                user_cache[check_uid] = u.get("username") or u.get("nickname") or check_uid
            except Exception:
                user_cache[check_uid] = check_uid
        username_check = user_cache.get(check_uid, "").lower()
        if username_check and username_check != "workflow":
            last_non_workflow_uid = check_uid
            last_non_workflow_msg = (p.get("message") or "").replace("\n", " ").strip()[:500]
            break
    
    # Use non-workflow if found
    if last_non_workflow_uid:
        uid = last_non_workflow_uid
        if msg != last_non_workflow_msg:
            msg = last_non_workflow_msg
    
    if uid and uid not in user_cache:
        try:
            u = client.get_user(uid)
            user_cache[uid] = u.get("username") or u.get("nickname") or uid
        except Exception:
            user_cache[uid] = uid
    
    parts = []
    for pid in order:
        p = posts.get(pid)
        if p and p.get("message"):
            parts.append(str(p["message"]))
    case.thread_search_text = "\n".join(parts)[:12000]
    case.last_activity_at_ms = max(case.last_activity_at_ms, create_at)
    case.last_message_user_id = uid
    username_display = user_cache.get(uid, "")
    # Don't show "workflow" as username
    case.last_message_username = username_display if username_display.lower() != "workflow" else ""
    case.last_message_preview = msg
    case.updated_at = utcnow()
    db.add(case)


def _ingest_posts_for_single_channel(
    db: Session,
    col: KanbanColumn,
    channel_id: str,
    client: TimeClient,
    me: dict[str, Any],
    user_cache: dict[str, str],
) -> int:
    cursor = _get_or_create_cursor(db, channel_id)
    since = cursor.since_ms
    try:
        bundle = client.get_posts_since(channel_id, since)
    except Exception:
        raise
    posts: dict[str, Any] = bundle.get("posts") or {}
    order: list[str] = bundle.get("order") or []
    my_id = me.get("id") or ""
    my_username = me.get("username") or ""
    rules = col.rules()

    created, max_seen = _ingest_bundle_into_cases(
        db,
        col,
        channel_id,
        client,
        my_id,
        my_username,
        user_cache,
        rules,
        order,
        posts,
        since,
    )

    cursor.since_ms = max(cursor.since_ms, max_seen)
    db.add(cursor)
    db.commit()
    return created


def ingest_posts_for_column(
    db: Session,
    col: KanbanColumn,
    client: TimeClient,
    me: dict[str, Any],
    user_cache: dict[str, str],
) -> int:
    channel_ids = resolve_all_channel_ids(db, col, client)
    total = 0
    for cid in channel_ids:
        total += _ingest_posts_for_single_channel(db, col, cid, client, me, user_cache)
    return total


def _ingest_historical_for_single_channel(
    db: Session,
    col: KanbanColumn,
    channel_id: str,
    client: TimeClient,
    me: dict[str, Any],
    user_cache: dict[str, str],
    pages: int,
    reset_cursor: bool,
) -> int:
    cursor = _get_or_create_cursor(db, channel_id)
    if reset_cursor:
        cursor.since_ms = 0
        db.add(cursor)
        db.commit()
    since_floor = cursor.since_ms
    my_id = me.get("id") or ""
    my_username = me.get("username") or ""
    rules = col.rules()
    total_created = 0
    before: str | None = None
    cap = max(1, min(pages, 2000))
    seen_roots: set[str] = set()

    for _ in range(cap):
        if before:
            bundle = client.get_posts_before(channel_id, before, 200)
        else:
            bundle = client.get_posts_latest(channel_id, 200)
        order: list[str] = bundle.get("order") or []
        posts: dict[str, Any] = bundle.get("posts") or {}
        if not order:
            break
        before = order[-1]
        created, batch_max = _ingest_bundle_into_cases(
            db,
            col,
            channel_id,
            client,
            my_id,
            my_username,
            user_cache,
            rules,
            order,
            posts,
            since_floor,
            seen_roots,
        )
        since_floor = max(since_floor, batch_max)
        total_created += created
        db.commit()

    cursor.since_ms = max(cursor.since_ms, since_floor)
    db.add(cursor)
    db.commit()
    return total_created


def ingest_historical_for_column(
    db: Session,
    col: KanbanColumn,
    client: TimeClient,
    me: dict[str, Any],
    user_cache: dict[str, str],
    pages: int,
    reset_cursor: bool = False,
) -> int:
    """Подгружает более старые посты через пагинацию before= по каждому каналу колонки."""
    channel_ids = resolve_all_channel_ids(db, col, client)
    total = 0
    for cid in channel_ids:
        total += _ingest_historical_for_single_channel(
            db, col, cid, client, me, user_cache, pages, reset_cursor
        )
    return total


def run_full_sync(db: Session, access_token: str, column_id: str | None = None) -> dict[str, Any]:
    client = TimeClient(access_token)
    me = client.get_me()
    user_cache: dict[str, str] = {}
    q = db.query(KanbanColumn).order_by(KanbanColumn.position, KanbanColumn.created_at)
    cols = [c for c in q.all() if parse_targets_json(c)]
    if column_id:
        cols = [c for c in cols if c.id == column_id]
    total_new = 0
    errors: list[str] = []
    for col in cols:
        try:
            total_new += ingest_posts_for_column(db, col, client, me, user_cache)
        except Exception as e:
            errors.append(f"{col.title}: {e}")

    # refresh thread text for active cases (batch, limit)
    active = (
        db.query(Case)
        .filter(Case.status == "active")
        .order_by(Case.updated_at.desc())
        .limit(200)
        .all()
    )
    for case in active:
        try:
            refresh_case_from_thread(db, case, client, user_cache)
        except Exception:
            pass
    db.commit()
    return {"new_cases": total_new, "errors": errors, "me": {"id": me.get("id"), "username": me.get("username")}}


def run_history_sync(
    db: Session,
    access_token: str,
    pages: int = 10,
    column_id: str | None = None,
    reset_cursor: bool = False,
) -> dict[str, Any]:
    client = TimeClient(access_token)
    me = client.get_me()
    user_cache: dict[str, str] = {}
    q = db.query(KanbanColumn).order_by(KanbanColumn.position, KanbanColumn.created_at)
    cols = [c for c in q.all() if parse_targets_json(c)]
    if column_id:
        cols = [c for c in cols if c.id == column_id]
    total_new = 0
    errors: list[str] = []
    for col in cols:
        try:
            total_new += ingest_historical_for_column(
                db, col, client, me, user_cache, pages, reset_cursor=reset_cursor
            )
        except Exception as e:
            errors.append(f"{col.title}: {e}")
    return {"new_cases": total_new, "errors": errors, "pages": pages, "reset_cursor": reset_cursor}


def _reporter_substrings_from_me(me: dict[str, Any] | None) -> list[str]:
    if not me:
        return []
    subs: list[str] = []
    u = (me.get("username") or "").strip()
    if u:
        subs.append(f"@{u}")
    first = (me.get("first_name") or me.get("FirstName") or "").strip()
    last = (me.get("last_name") or me.get("LastName") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        subs.append(full.lower())
    if last:
        low = last.lower()
        if low not in (x.lower() for x in subs):
            subs.append(low)
    nick = (me.get("nickname") or "").strip()
    if nick:
        nl = nick.lower()
        if nl not in (x.lower() for x in subs):
            subs.append(nl)
    seen: set[str] = set()
    out: list[str] = []
    for s in subs:
        s = (s or "").strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def default_rules_json(me: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {
            # AMA/бэкенд-чаты используют шаблон ds_ama; только support режет всё остальное
            "templates": ["support", "ds_ama"],
            "match_mentions_me": True,
            "require_addressed_to_me": True,
            "extra_names": [],
            "reporter_substrings": _reporter_substrings_from_me(me),
            "support_intro_required": True,
        },
        ensure_ascii=False,
    )
