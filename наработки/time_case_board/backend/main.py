from __future__ import annotations

import json
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy.orm import Session

from backend.accounts import (
    board_db_path,
    list_local_board_accounts,
    read_active_account_id,
    resolve_active_board_account_id,
    write_active_account_id,
)
from backend.auth_tokens import get_access_token_for_api, get_stored_token_row, save_token_response
from backend.config import apply_jira_runtime, merge_project_dotenv, settings
from backend.database import (
    Case, IgnoredThread, JiraNotifyState, JiraWatcher, KanbanColumn,
    NotificationSettings, get_db, get_sessionmaker,
)
from backend.oauth_time import exchange_authorization_code
from backend.parsers import build_parsed_fields, post_matches_column_rules
from backend.column_channels import (
    column_allows_post_channel,
    parse_targets_json,
    resolve_all_channel_ids,
    serialize_targets,
    targets_from_parsed_urls,
)
from backend.sync_service import default_rules_json, run_full_sync, run_history_sync
from backend.time_client import TimeClient, root_post_id_from
from backend.url_parse import parse_channel_url, parse_post_permalink
from backend import jira_client, jira_write
from backend.time_notify import start_poll_task, stop_poll_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_poll_task()
    yield
    stop_poll_task()


app = FastAPI(title="TiMe Case Board", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://{settings.app_host}:{settings.app_port}",
        f"http://127.0.0.1:{settings.app_port}",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_oauth_states: dict[str, str] = {}


class ColumnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    title: str
    team_name: str = "tinkoff"
    channel_url: Optional[str] = Field(default=None, description="Один канал: .../team/channels/name")
    channel_urls: Optional[list[str]] = Field(default=None, description="Несколько каналов — полные URL по одному")
    rules_json: Optional[str] = None

    @model_validator(mode="after")
    def validate_channel_source(self):
        has_single = self.channel_url and self.channel_url.strip()
        has_multi = self.channel_urls and len([u for u in self.channel_urls if u and str(u).strip()]) > 0
        if not (has_single or has_multi):
            raise ValueError("Укажите channel_url или непустой channel_urls")
        return self


class ColumnPatch(BaseModel):
    title: str | None = None
    team_name: str | None = None
    channel_name: str | None = None
    rules_json: str | None = None
    position: int | None = None
    channel_urls: list[str] | None = Field(None, description="Заменить список каналов колонки (полные URL)")


class CasePatch(BaseModel):
    status: str | None = None
    initiator: str | None = None
    column_id: str | None = None


class ManualCaseCreate(BaseModel):
    column_id: str
    permalink: str
    initiator: str = "unspecified"  # self | incoming | unspecified
    custom_title: str | None = None


class SyncHistoryBody(BaseModel):
    pages: int = 10
    column_id: str | None = None
    reset_cursor: bool = False


class SetActiveAccountBody(BaseModel):
    account_id: str = Field(..., min_length=1)


class JiraConfigureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_base_url: str = Field(..., min_length=8)
    jira_token: str = Field(..., min_length=4)


class JiraCommentBody(BaseModel):
    text: str = Field(..., min_length=1)


class JiraTransitionBody(BaseModel):
    transition_id: str = Field(..., min_length=1)


class JiraAssignBody(BaseModel):
    assignee_name: Optional[str] = None


class NotificationSettingsBody(BaseModel):
    time_channel_url: str = ""
    enabled: bool = False
    notify_new_comments: bool = True
    poll_interval_sec: int = Field(default=180, ge=60, le=3600)


class JiraWatcherCreate(BaseModel):
    label: str = ""
    jql: str = Field(..., min_length=1)
    watcher_type: str = "custom"
    enabled: bool = True


class JiraWatcherPatch(BaseModel):
    label: Optional[str] = None
    jql: Optional[str] = None
    watcher_type: Optional[str] = None
    enabled: Optional[bool] = None
    position: Optional[int] = None


def _case_to_dict(c: Case) -> dict[str, Any]:
    return {
        "id": c.id,
        "column_id": c.column_id,
        "root_post_id": c.root_post_id,
        "channel_id": c.channel_id,
        "team_name": c.team_name,
        "status": c.status,
        "initiator": c.initiator,
        "root_author_user_id": c.root_author_user_id or "",
        "site_id": c.site_id,
        "assignee_raw": c.assignee_raw,
        "title_preview": c.title_preview,
        "permalink": c.permalink,
        "thread_search_text": c.thread_search_text,
        "last_activity_at_ms": c.last_activity_at_ms,
        "last_message_username": c.last_message_username,
        "last_message_preview": c.last_message_preview,
        "manual": c.manual,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _column_to_dict(col: KanbanColumn) -> dict[str, Any]:
    targets = parse_targets_json(col)
    return {
        "id": col.id,
        "title": col.title,
        "team_name": col.team_name,
        "channel_name": col.channel_name,
        "channel_id": col.channel_id,
        "channels_json": getattr(col, "channels_json", "") or "",
        "channels": [
            {"team_name": t["team_name"], "channel_name": t["channel_name"], "channel_id": t.get("channel_id", "")}
            for t in targets
        ],
        "position": col.position,
        "rules_json": col.rules_json,
    }


def _site_id_matches_case(c: Case, sid: str) -> bool:
    """Совпадение по полю site_id или целому числу в тексте (не 1224 при поиске 224)."""
    s = sid.strip()
    if not s:
        return True
    if (c.site_id or "").strip() == s:
        return True
    pat = re.compile(r"(?<!\d)" + re.escape(s) + r"(?!\d)")
    for blob in (c.raw_root_message or "", c.thread_search_text or "", c.title_preview or ""):
        if pat.search(blob):
            return True
    return False


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status():
    pat = bool((settings.time_personal_access_token or "").strip())
    oauth_ok = bool(settings.oauth_client_id and settings.oauth_redirect_uri)
    account_id = resolve_active_board_account_id()
    username: str | None = None
    logged_in = False
    if pat:
        from backend.accounts import get_me_for_personal_token

        me = get_me_for_personal_token()
        if me:
            logged_in = True
            username = me.get("username") or None
    elif account_id:
        db = get_sessionmaker(board_db_path(account_id))()
        try:
            row = get_stored_token_row(db)
            logged_in = bool(row and (row.access_token or "").strip())
            username = (row.username if row else "") or None
        finally:
            db.close()
    return {
        "personal_token_configured": pat,
        "oauth_configured": oauth_ok,
        "logged_in": logged_in,
        "username": username,
        "account_id": account_id,
        "active_account_id": read_active_account_id(),
        "multi_account_switch_disabled": pat,
    }


@app.get("/api/auth/accounts")
def auth_accounts_list():
    return {"accounts": list_local_board_accounts()}


@app.post("/api/auth/active")
def auth_set_active(body: SetActiveAccountBody):
    if (settings.time_personal_access_token or "").strip():
        raise HTTPException(
            400,
            "С TIME_PERSONAL_ACCESS_TOKEN в oauth.env активна только одна учётка. "
            "Уберите переменную, чтобы переключаться между OAuth-учётками.",
        )
    aid = body.account_id.strip()
    path = board_db_path(aid)
    if not path.is_file():
        raise HTTPException(404, "Локального файла доски для этой учётки нет — сначала войдите через TiMe.")
    write_active_account_id(aid)
    return {"ok": True, "account_id": aid}


@app.post("/api/auth/disconnect")
def auth_disconnect_oauth(db: Session = Depends(get_db)):
    """Сбрасывает OAuth-токены в БД текущей доски (PAT из env не трогаем)."""
    if (settings.time_personal_access_token or "").strip():
        raise HTTPException(400, "При использовании TIME_PERSONAL_ACCESS_TOKEN отключение OAuth не применимо.")
    row = get_stored_token_row(db)
    if row:
        row.access_token = ""
        row.refresh_token = ""
        row.expires_at_ms = None
        db.add(row)
        db.commit()
    return {"ok": True}


@app.get("/api/time/profile")
def time_profile(db: Session = Depends(get_db)):
    """Профиль из TiMe /users/me — для подстановки имён в правила колонки."""
    token = _token(db)
    return TimeClient(token).get_me()


@app.get("/api/jira/status")
def jira_status():
    """Факт настройки, hostname и идентификаторы пользователя токена (для фильтра «последний комментарий мой»)."""
    from urllib.parse import urlparse

    if not jira_client.jira_configured():
        return {
            "configured": False,
            "jira_host": None,
            "user_hint": None,
            "jira_account_id": None,
            "jira_display_name": None,
            "jira_name": None,
        }
    host = None
    u = (settings.jira_base_url or "").strip()
    if u:
        host = urlparse(u).hostname
    out: dict[str, Any] = {
        "configured": True,
        "jira_host": host,
        "user_hint": None,
        "jira_account_id": None,
        "jira_display_name": None,
        "jira_name": None,
    }
    try:
        me = jira_client.get_myself()
        dn = me.get("displayName") or me.get("name")
        nm = me.get("name") or me.get("key")
        out["user_hint"] = str(dn or nm or "").strip() or None
        aid = me.get("accountId")
        out["jira_account_id"] = str(aid).strip() if aid else None
        out["jira_display_name"] = str(me.get("displayName") or "").strip() or None
        out["jira_name"] = str(nm).strip() if nm else None
    except jira_client.JiraApiError:
        pass
    return out


@app.get("/api/jira/ping")
def jira_ping():
    """Один запрос к /myself — проверка токена под VPN (вызывать вручную)."""
    try:
        me = jira_client.get_myself()
        return {
            "ok": True,
            "name": me.get("name") or me.get("key"),
            "display_name": me.get("displayName"),
        }
    except jira_client.JiraApiError as e:
        raise HTTPException(status_code=e.status if e.status < 600 else 502, detail=e.message)


@app.post("/api/jira/configure")
def jira_configure(body: JiraConfigureBody):
    """
    Сохранить JIRA_BASE_URL и JIRA_TOKEN в локальный .env (не в git) и применить без перезапуска.
    Только для локального доверенного окружения.
    """
    from urllib.parse import urlparse

    u = body.jira_base_url.strip().rstrip("/")
    if not (u.startswith("http://") or u.startswith("https://")):
        raise HTTPException(400, detail="URL должен начинаться с http:// или https://")
    parsed = urlparse(u)
    if not parsed.hostname:
        raise HTTPException(400, detail="Некорректный хост в URL")
    tok = body.jira_token.strip()
    merge_project_dotenv({"JIRA_BASE_URL": u, "JIRA_TOKEN": tok})
    apply_jira_runtime(u, tok)
    return {"ok": True, "jira_host": parsed.hostname}


@app.get("/api/jira/issues")
def jira_issues(
    start_at: int = Query(0, ge=0),
    max_results: int = Query(100, ge=1, le=100, description="Размер одной страницы Jira API (макс. 100)"),
    max_fetch: int = Query(
        800,
        ge=1,
        le=2000,
        description="Сколько задач максимум подтянуть (несколько страниц, если total больше)",
    ),
    jql: str | None = None,
):
    if not jira_client.jira_configured():
        raise HTTPException(
            503,
            detail="Jira не настроена: задайте JIRA_BASE_URL и JIRA_TOKEN в локальном oauth.env (не коммитьте).",
        )
    q = (jql or settings.jira_jql).strip()
    try:
        return jira_client.search_issues(
            q,
            start_at=start_at,
            max_results=max_results,
            max_fetch=max_fetch,
        )
    except jira_client.JiraApiError as e:
        code = e.status if 400 <= e.status < 600 else 502
        raise HTTPException(status_code=code, detail=e.message)


@app.get("/oauth/login")
def oauth_login():
    if not settings.oauth_client_id:
        raise HTTPException(400, "OAuth client_id not configured")
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "1"
    from urllib.parse import urlencode

    q = urlencode(
        {
            "client_id": settings.oauth_client_id,
            "redirect_uri": settings.oauth_redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )
    return RedirectResponse(f"{settings.oauth_authorize_url}?{q}")


@app.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = ""):
    if state not in _oauth_states:
        raise HTTPException(400, "invalid state")
    _oauth_states.pop(state, None)
    if not code:
        raise HTTPException(400, "missing code")
    data = exchange_authorization_code(code)
    token = data.get("access_token") or ""
    if not token:
        raise HTTPException(400, "no access_token in token response")
    try:
        me = TimeClient(token).get_me()
    except Exception as e:
        raise HTTPException(502, f"TiMe /users/me: {e}") from e
    uid = str(me.get("id") or "").strip()
    if not uid:
        raise HTTPException(502, "TiMe не вернул id пользователя")
    path = board_db_path(uid)
    db = get_sessionmaker(path)()
    try:
        save_token_response(db, data)
        row = get_stored_token_row(db)
        if row:
            row.user_id = uid
            row.username = me.get("username") or ""
            db.add(row)
            db.commit()
        write_active_account_id(uid, row.username if row else None)
    finally:
        db.close()
    return RedirectResponse(f"http://{settings.app_host}:{settings.app_port}/")


@app.get("/api/columns")
def list_columns(db: Session = Depends(get_db)):
    rows = db.query(KanbanColumn).order_by(KanbanColumn.position, KanbanColumn.created_at).all()
    return [_column_to_dict(c) for c in rows]


@app.post("/api/columns")
def create_column(body: ColumnCreate, db: Session = Depends(get_db)):
    raw_urls: list[str] = []
    if body.channel_urls:
        raw_urls = [u.strip() for u in body.channel_urls if u and str(u).strip()]
    elif body.channel_url and body.channel_url.strip():
        raw_urls = [body.channel_url.strip()]
    # Валидатор модели уже проверил, что хотя бы одно поле указано
    parsed_list = []
    for u in raw_urls:
        p = parse_channel_url(u)
        if not p:
            hint = u if len(u) <= 100 else u[:100] + "…"
            raise HTTPException(400, f"URL не распознан: {hint} (нужен .../team/channels/name)")
        parsed_list.append(p)
    targets = targets_from_parsed_urls(parsed_list)
    me: dict[str, Any] | None = None
    if body.rules_json is None:
        try:
            me = TimeClient(_token(db)).get_me()
        except Exception:
            me = None
    rules = body.rules_json if body.rules_json is not None else default_rules_json(me)
    try:
        json.loads(rules)
    except json.JSONDecodeError:
        raise HTTPException(400, "rules_json не валидный JSON")
    max_pos = db.query(KanbanColumn).count()
    first = targets[0]
    col = KanbanColumn(
        title=body.title.strip(),
        team_name=first["team_name"],
        channel_name=first["channel_name"],
        channel_id="",
        # Всегда храним полный список в JSON — иначе мультиканал теряется и в шапке один #channel
        channels_json=serialize_targets(targets),
        position=max_pos,
        rules_json=rules,
    )
    db.add(col)
    db.commit()
    db.refresh(col)
    return _column_to_dict(col)


@app.patch("/api/columns/{column_id}")
def patch_column(column_id: str, body: ColumnPatch, db: Session = Depends(get_db)):
    col = db.query(KanbanColumn).filter(KanbanColumn.id == column_id).first()
    if not col:
        raise HTTPException(404)
    if body.title is not None:
        col.title = body.title
    if body.team_name is not None:
        col.team_name = body.team_name
    if body.channel_urls is not None:
        raw_urls = [u.strip() for u in body.channel_urls if u and str(u).strip()]
        if not raw_urls:
            raise HTTPException(400, "channel_urls пустой")
        parsed_list = []
        for u in raw_urls:
            p = parse_channel_url(u)
            if not p:
                hint = u if len(u) <= 100 else u[:100] + "…"
                raise HTTPException(400, f"URL не распознан: {hint}")
            parsed_list.append(p)
        targets = targets_from_parsed_urls(parsed_list)
        first = targets[0]
        col.team_name = first["team_name"]
        col.channel_name = first["channel_name"]
        col.channel_id = ""
        col.channels_json = serialize_targets(targets)
    elif body.channel_name is not None:
        col.channel_name = body.channel_name
        col.channel_id = ""
        col.channels_json = json.dumps(
            [{"team_name": col.team_name, "channel_name": col.channel_name.strip(), "channel_id": ""}],
            ensure_ascii=False,
        )
    if body.rules_json is not None:
        try:
            json.loads(body.rules_json)
        except json.JSONDecodeError:
            raise HTTPException(400, "rules_json не валидный JSON")
        col.rules_json = body.rules_json
    if body.position is not None:
        col.position = body.position
    db.add(col)
    db.commit()
    return _column_to_dict(col)


@app.post("/api/columns/{column_id}/prune")
def prune_column_cases(column_id: str, db: Session = Depends(get_db)):
    """Удаляет карточки колонки, корневой текст которых больше не проходит rules_json."""
    col = db.query(KanbanColumn).filter(KanbanColumn.id == column_id).first()
    if not col:
        raise HTTPException(404)
    token = _token(db)
    me = TimeClient(token).get_me()
    rules = col.rules()
    my_u = me.get("username") or ""
    my_id = me.get("id") or ""
    removed = 0
    for case in db.query(Case).filter(Case.column_id == column_id).all():
        msg = case.raw_root_message or ""
        if not post_matches_column_rules(msg, rules, my_u, my_id, []):
            db.delete(case)
            removed += 1
    db.commit()
    return {"removed": removed}


@app.delete("/api/columns/{column_id}")
def delete_column(column_id: str, db: Session = Depends(get_db)):
    col = db.query(KanbanColumn).filter(KanbanColumn.id == column_id).first()
    if not col:
        raise HTTPException(404)
    db.delete(col)
    db.commit()
    return {"ok": True}


@app.get("/api/cases")
def list_cases(
    column_id: str | None = None,
    q: str | None = None,
    site_id: str | None = None,
    initiator: str | None = None,
    include_resolved: bool = False,
    my_username: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Case)
    if column_id:
        query = query.filter(Case.column_id == column_id)
    if not include_resolved:
        query = query.filter(Case.status == "active")
    if initiator in ("self", "incoming", "unspecified"):
        query = query.filter(Case.initiator == initiator)
    rows = query.all()
    # site_id may be comma-separated list ("308,6321,2262")
    if site_id and site_id.strip():
        ids = [s.strip() for s in site_id.split(",") if s.strip()]
        rows = [c for c in rows if any(_site_id_matches_case(c, sid) for sid in ids)]
    if q:
        ql = q.lower()
        rows = [c for c in rows if ql in (c.thread_search_text or "").lower() or ql in (c.title_preview or "").lower() or ql in (c.raw_root_message or "").lower()]
    if my_username and my_username.strip():
        # Устар.: раньше искали @login в тексте. Оставлено для совместимости API; UI не передаёт.
        un = my_username.strip().lower()
        un_at = f"@{un}"
        rows = [
            c for c in rows
            if (
                un_at in (c.raw_root_message or "").lower()
                or un_at in (c.thread_search_text or "").lower()
                or c.initiator == "self"
            )
        ]
    rows.sort(key=lambda c: c.last_activity_at_ms, reverse=True)
    return [_case_to_dict(c) for c in rows]


@app.patch("/api/cases/{case_id}")
def patch_case(case_id: str, body: CasePatch, db: Session = Depends(get_db)):
    c = db.query(Case).filter(Case.id == case_id).first()
    if not c:
        raise HTTPException(404)
    if body.status is not None:
        if body.status not in ("active", "resolved"):
            raise HTTPException(400, "status must be active|resolved")
        c.status = body.status
    if body.initiator is not None:
        if body.initiator not in ("self", "incoming", "unspecified"):
            raise HTTPException(400, "initiator invalid")
        c.initiator = body.initiator
    if body.column_id is not None:
        col = db.query(KanbanColumn).filter(KanbanColumn.id == body.column_id).first()
        if not col:
            raise HTTPException(400, "column not found")
        c.column_id = body.column_id
    db.add(c)
    db.commit()
    return _case_to_dict(c)


@app.post("/api/cases/manual")
def create_manual_case(body: ManualCaseCreate, db: Session = Depends(get_db)):
    col = db.query(KanbanColumn).filter(KanbanColumn.id == body.column_id).first()
    if not col:
        raise HTTPException(404, "column not found")
    parsed = parse_post_permalink(body.permalink)
    if not parsed:
        raise HTTPException(400, "permalink не распознан (.../team/pl/postId)")
    token = _token(db)
    client = TimeClient(token)
    resolve_all_channel_ids(db, col, client)
    db.refresh(col)
    post = client.get_post(parsed.post_id)
    post_channel_id = str(post.get("channel_id") or "")
    if not column_allows_post_channel(db, col, client, post_channel_id):
        raise HTTPException(400, "пост из другого канала, чем колонка")
    root_id = root_post_id_from(post)
    if db.query(Case).filter(Case.channel_id == post_channel_id, Case.root_post_id == root_id).first():
        raise HTTPException(400, "кейс уже есть")
    initiator = body.initiator
    if initiator not in ("self", "incoming", "unspecified"):
        raise HTTPException(400, "initiator invalid")
    root_post = client.get_post(root_id) if root_id != post.get("id") else post
    msg = (root_post.get("message") or post.get("message") or "")
    fields = build_parsed_fields(msg, col.rules())
    site_parsed = str(fields.get("site_id")) if fields.get("site_id") else None
    title_pv = (body.custom_title.strip() if body.custom_title and body.custom_title.strip() else None) or (fields.get("description") or msg)[:1024]
    team_for_link = (parsed.team_name or col.team_name).strip() or "tinkoff"
    rauth = str(root_post.get("user_id") or "")
    c = Case(
        column_id=col.id,
        root_post_id=root_id,
        channel_id=post_channel_id,
        team_name=team_for_link,
        status="active",
        initiator=initiator,
        root_author_user_id=rauth,
        permalink=f"{settings.time_base_url.rstrip('/')}/{team_for_link}/pl/{root_id}",
        title_preview=title_pv,
        raw_root_message=msg,
        site_id=site_parsed,
        manual=True,
        last_activity_at_ms=int(post.get("create_at") or 0),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _case_to_dict(c)


def _token(db: Session) -> str:
    try:
        return get_access_token_for_api(db)
    except PermissionError:
        raise HTTPException(401, "Нужна авторизация: откройте /oauth/login или задайте TIME_PERSONAL_ACCESS_TOKEN")


@app.post("/api/sync")
def api_sync(db: Session = Depends(get_db)):
    token = _token(db)
    return run_full_sync(db, token)


@app.post("/api/columns/{column_id}/sync")
def api_column_sync(column_id: str, db: Session = Depends(get_db)):
    col = db.query(KanbanColumn).filter(KanbanColumn.id == column_id).first()
    if not col:
        raise HTTPException(404, "column not found")
    token = _token(db)
    return run_full_sync(db, token, column_id=column_id)


@app.post("/api/columns/{column_id}/history-reset")
def api_column_history_reset(
    column_id: str,
    pages: int = Query(50, ge=1, le=2000, description="Страниц по ~200 постов (TiMe before=)"),
    db: Session = Depends(get_db),
):
    """Сбрасывает курсор канала и подгружает историю (как «История» + «с нуля» для этой колонки)."""
    col = db.query(KanbanColumn).filter(KanbanColumn.id == column_id).first()
    if not col:
        raise HTTPException(404, "column not found")
    token = _token(db)
    return run_history_sync(db, token, pages=pages, column_id=column_id, reset_cursor=True)


def _run_history_sync(body: SyncHistoryBody, db: Session) -> dict[str, Any]:
    token = _token(db)
    pages = max(1, min(body.pages, 2000))
    return run_history_sync(db, token, pages=pages, column_id=body.column_id, reset_cursor=body.reset_cursor)


@app.post("/api/sync/history")
def api_sync_history(body: SyncHistoryBody, db: Session = Depends(get_db)):
    return _run_history_sync(body, db)


@app.post("/api/history/pull")
def api_history_pull(body: SyncHistoryBody, db: Session = Depends(get_db)):
    """Тот же смысл, что /api/sync/history — короткий путь (на случай прокси/старых билдов)."""
    return _run_history_sync(body, db)


@app.post("/api/cases/{case_id}/resolve")
def resolve_case(case_id: str, db: Session = Depends(get_db)):
    c = db.query(Case).filter(Case.id == case_id).first()
    if not c:
        raise HTTPException(404)
    c.status = "resolved"
    db.add(c)
    db.commit()
    return _case_to_dict(c)


@app.post("/api/cases/{case_id}/ignore_forever")
def ignore_forever(case_id: str, db: Session = Depends(get_db)):
    c = db.query(Case).filter(Case.id == case_id).first()
    if not c:
        raise HTTPException(404)
    db.add(IgnoredThread(channel_id=c.channel_id, root_post_id=c.root_post_id))
    db.delete(c)
    db.commit()
    return {"ok": True}


# ── Jira write actions ────────────────────────────────────────────────────────

@app.get("/api/jira/issues/{issue_key}/transitions")
def get_jira_transitions(issue_key: str):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    try:
        return {"transitions": jira_write.get_transitions(issue_key)}
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


@app.post("/api/jira/issues/{issue_key}/comment")
def post_jira_comment(issue_key: str, body: JiraCommentBody):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    try:
        result = jira_write.add_comment(issue_key, body.text)
        return {"ok": True, "id": result.get("id"), "created": result.get("created")}
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


@app.post("/api/jira/issues/{issue_key}/comment-with-images")
async def post_jira_comment_with_images(
    issue_key: str,
    comment: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    try:
        comment_result: dict[str, Any] = {}
        if comment.strip():
            comment_result = jira_write.add_comment(issue_key, comment.strip())
        attachment_names: list[str] = []
        for f in files:
            content = await f.read()
            if not content:
                continue
            ct = f.content_type or "application/octet-stream"
            fname = f.filename or "attachment"
            jira_write.add_attachment(issue_key, fname, content, ct)
            attachment_names.append(fname)
        return {
            "ok": True,
            "comment_id": comment_result.get("id"),
            "attachments": attachment_names,
        }
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


@app.post("/api/jira/issues/{issue_key}/transition")
def do_jira_transition(issue_key: str, body: JiraTransitionBody):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    try:
        jira_write.transition_issue(issue_key, body.transition_id)
        return {"ok": True}
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


@app.post("/api/jira/issues/{issue_key}/assign")
def do_jira_assign(issue_key: str, body: JiraAssignBody):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    try:
        jira_write.assign_issue(issue_key, body.assignee_name)
        return {"ok": True}
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


@app.get("/api/jira/users")
def search_jira_users(q: str = Query(default="", min_length=0)):
    if not jira_client.jira_configured():
        raise HTTPException(503, "Jira не настроена")
    if not q.strip():
        return {"users": []}
    try:
        return {"users": jira_write.search_users(q.strip())}
    except jira_client.JiraApiError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, e.message)


# ── Notification settings ─────────────────────────────────────────────────────

def _notif_to_dict(ns: NotificationSettings) -> dict[str, Any]:
    return {
        "time_channel_url": ns.time_channel_url or "",
        "time_channel_id": ns.time_channel_id or "",
        "enabled": bool(ns.enabled),
        "notify_new_comments": bool(ns.notify_new_comments),
        "poll_interval_sec": ns.poll_interval_sec or 180,
    }


@app.get("/api/notifications/settings")
def get_notification_settings(db: Session = Depends(get_db)):
    ns = db.query(NotificationSettings).first()
    if not ns:
        return {
            "time_channel_url": "",
            "time_channel_id": "",
            "enabled": False,
            "notify_new_comments": True,
            "poll_interval_sec": 180,
        }
    return _notif_to_dict(ns)


@app.post("/api/notifications/settings")
def save_notification_settings(body: NotificationSettingsBody, db: Session = Depends(get_db)):
    ns = db.query(NotificationSettings).first()
    if not ns:
        ns = NotificationSettings()
        db.add(ns)

    channel_url = body.time_channel_url.strip()
    channel_id = ns.time_channel_id or ""

    # Resolve channel_id if URL changed
    if channel_url and channel_url != (ns.time_channel_url or ""):
        try:
            token = _token(db)
            from backend.time_notify import resolve_channel_id_from_url
            channel_id = resolve_channel_id_from_url(token, channel_url)
        except Exception as e:
            raise HTTPException(400, f"Не удалось получить channel_id из URL: {e}")
    elif not channel_url:
        channel_id = ""

    ns.time_channel_url = channel_url
    ns.time_channel_id = channel_id
    ns.enabled = body.enabled
    ns.notify_new_comments = body.notify_new_comments
    ns.poll_interval_sec = body.poll_interval_sec
    db.commit()
    return _notif_to_dict(ns)


@app.post("/api/notifications/test")
def test_notification(db: Session = Depends(get_db)):
    ns = db.query(NotificationSettings).first()
    if not ns or not ns.time_channel_id:
        raise HTTPException(400, "Канал Time не настроен — сначала сохраните настройки с URL канала")
    try:
        token = _token(db)
        from backend.time_notify import send_time_post
        send_time_post(token, ns.time_channel_id, "Тест уведомлений Time Case Board — всё работает!")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/api/notifications/poll")
async def trigger_poll_now():
    """Trigger an immediate poll cycle (for manual testing)."""
    try:
        import asyncio
        from backend.time_notify import _do_poll_sync
        await asyncio.to_thread(_do_poll_sync)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(502, str(e))


# ── Jira watchers ─────────────────────────────────────────────────────────────

def _watcher_to_dict(w: JiraWatcher) -> dict[str, Any]:
    return {
        "id": w.id,
        "label": w.label or "",
        "jql": w.jql or "",
        "watcher_type": w.watcher_type or "custom",
        "enabled": bool(w.enabled),
        "position": w.position,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@app.get("/api/jira/watchers")
def list_jira_watchers(db: Session = Depends(get_db)):
    rows = db.query(JiraWatcher).order_by(JiraWatcher.position, JiraWatcher.created_at).all()
    return [_watcher_to_dict(w) for w in rows]


@app.post("/api/jira/watchers")
def create_jira_watcher(body: JiraWatcherCreate, db: Session = Depends(get_db)):
    count = db.query(JiraWatcher).count()
    w = JiraWatcher(
        label=body.label.strip(),
        jql=body.jql.strip(),
        watcher_type=body.watcher_type,
        enabled=body.enabled,
        position=count,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _watcher_to_dict(w)


@app.patch("/api/jira/watchers/{watcher_id}")
def update_jira_watcher(watcher_id: str, body: JiraWatcherPatch, db: Session = Depends(get_db)):
    w = db.query(JiraWatcher).filter(JiraWatcher.id == watcher_id).first()
    if not w:
        raise HTTPException(404)
    if body.label is not None:
        w.label = body.label.strip()
    if body.jql is not None:
        w.jql = body.jql.strip()
    if body.watcher_type is not None:
        w.watcher_type = body.watcher_type
    if body.enabled is not None:
        w.enabled = body.enabled
    if body.position is not None:
        w.position = body.position
    db.add(w)
    db.commit()
    return _watcher_to_dict(w)


@app.delete("/api/jira/watchers/{watcher_id}")
def delete_jira_watcher(watcher_id: str, db: Session = Depends(get_db)):
    w = db.query(JiraWatcher).filter(JiraWatcher.id == watcher_id).first()
    if not w:
        raise HTTPException(404)
    db.delete(w)
    db.commit()
    return {"ok": True}


@app.delete("/api/jira/watchers/{watcher_id}/state")
def reset_watcher_state(watcher_id: str, db: Session = Depends(get_db)):
    """Clear last-seen state for a watcher so next poll starts fresh."""
    w = db.query(JiraWatcher).filter(JiraWatcher.id == watcher_id).first()
    if not w:
        raise HTTPException(404)
    db.query(JiraNotifyState).filter(JiraNotifyState.watcher_id == watcher_id).delete()
    db.commit()
    return {"ok": True}


_root = Path(__file__).resolve().parents[1]
_dist = _root / "web" / "dist"


@app.get("/")
def spa_index():
    index = _dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    return FileResponse(_root / "backend" / "static_placeholder.html")


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    p = _dist / "favicon.svg"
    if p.is_file():
        return FileResponse(p, media_type="image/svg+xml")
    raise HTTPException(404)


if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")
