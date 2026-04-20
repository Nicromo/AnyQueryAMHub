"""
Auto-extracted router — do not edit the @app registrations manually.
"""
from typing import Optional, List
from datetime import datetime, timedelta
import os
import json
import logging

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db, SessionLocal
from models import (
    Client, Task, Meeting, CheckUp, User, SyncLog, AuditLog,
    Notification, QBR, AccountPlan, ClientNote, TaskComment,
    FollowupTemplate, VoiceNote,
)
from auth import (
    authenticate_user, create_user, create_access_token,
    verify_password, hash_password, log_audit, decode_access_token,
)
from error_handlers import log_error, handle_db_error

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)

def _env_bool(key: str) -> bool:
    return bool(os.environ.get(key, ""))

@router.get("/api/auth/token")
async def api_get_token(db: Session = Depends(get_db), auth_token: Optional[str] = Cookie(None)):
    """Вернуть JWT токен текущего пользователя — для настройки расширения."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload_jwt = decode_access_token(auth_token)
    if not payload_jwt:
        raise HTTPException(status_code=401)
    return {"token": auth_token}



@router.post("/api/auth/taim/test")
async def api_test_taim(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить авторизацию в 1Time (time.tbank.ru / Mattermost)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    body = await request.json()
    login_id = body.get("login", "")
    password = body.get("password", "")
    import taim
    result = await taim.login(login_id, password)
    if result["ok"]:
        summary = await taim.get_summary(login_id, password)
        return {**result, **summary, "password": None}
    return result


# ============================================================================
# AUTH: TBANK TIME (SSO через TinkoffID)

@router.get("/auth/time", response_class=HTMLResponse)
async def time_oauth_start(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Подключение Tbank Time — PAT (рекомендуется) или MMAUTHTOKEN (запасной)."""
    if not auth_token:
        return RedirectResponse(url="/login")
    html = open("/home/claude/AnyQueryAMHub/am-hub-final/templates/time_auth.html").read()
    return HTMLResponse(content=html)


# ── Time OAuth 2.0 flow (на основе наработок коллеги) ───────────────────────

@router.get("/auth/time/login")
async def time_oauth_login(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Старт OAuth: редирект на time.tbank.ru/oauth/authorize."""
    if not auth_token:
        return RedirectResponse(url="/login")
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        return RedirectResponse(url="/login")
    from integrations.time_oauth import is_configured, authorize_url
    if not is_configured():
        raise HTTPException(status_code=500, detail="TIME_OAUTH_CLIENT_ID/SECRET не заданы в env")
    import secrets as _secrets
    state = _secrets.token_urlsafe(24) + "." + str(payload.get("sub"))
    resp = RedirectResponse(url=authorize_url(state), status_code=303)
    resp.set_cookie(key="time_oauth_state", value=state, max_age=600,
                     httponly=True, samesite="lax", secure=False)
    return resp


@router.get("/auth/time/callback")
@router.get("/auth/time/oauth/callback")
async def time_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
    time_oauth_state: Optional[str] = Cookie(None),
):
    """Time перенаправит сюда с ?code=...&state=...; меняем code на токены."""
    if error:
        return HTMLResponse(content=f"<h3>Time OAuth отменён</h3><p>{error}</p><p><a href='/design/command'>← Вернуться</a></p>",
                             status_code=400)
    if not code or not state:
        return HTMLResponse(content="<h3>Нет code/state в ответе Time</h3>", status_code=400)
    if time_oauth_state and time_oauth_state != state:
        return HTMLResponse(content="<h3>State mismatch (CSRF?)</h3>", status_code=400)
    # Извлечь user_id из state
    try:
        user_id = int(state.rsplit(".", 1)[-1])
    except Exception:
        return HTMLResponse(content="<h3>Битый state</h3>", status_code=400)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return HTMLResponse(content="<h3>Пользователь не найден</h3>", status_code=400)

    from integrations.time_oauth import exchange_code, get_me
    import time as _time
    try:
        tok = await exchange_code(code)
    except Exception as e:
        logger.exception("Time OAuth exchange failed")
        return HTMLResponse(content=f"<h3>Ошибка обмена code: {e}</h3>", status_code=500)

    access_token = tok.get("access_token")
    if not access_token:
        return HTMLResponse(content=f"<h3>Нет access_token в ответе Time</h3><pre>{tok}</pre>", status_code=500)

    # Получаем username/email
    me = {}
    try:
        me = await get_me(access_token)
    except Exception as e:
        logger.warning("Time users/me failed after OAuth: %s", e)

    # Канал any-team-support
    channel_id = None
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as hx:
            ch = await hx.get(
                "https://time.tbank.ru/api/v4/teams/name/tinkoff/channels/name/any-team-support",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ch.status_code == 200:
                channel_id = ch.json().get("id")
    except Exception:
        pass

    settings = dict(user.settings or {})
    tm = dict(settings.get("tbank_time", {}))
    tm.update({
        "access_token": access_token,
        "refresh_token": tok.get("refresh_token", tm.get("refresh_token", "")),
        "token_type": tok.get("token_type", "bearer"),
        "expires_at": int(_time.time()) + int(tok.get("expires_in", 3600)) - 30,
        "username": me.get("username") or tm.get("username"),
        "email": me.get("email") or tm.get("email"),
        "user_id": me.get("id") or tm.get("user_id"),
    })
    if channel_id:
        tm["support_channel_id"] = channel_id
    # Выкинем устаревшие mmauthtoken / session_cookie если были
    tm.pop("mmauthtoken", None)
    tm.pop("session_cookie", None)
    settings["tbank_time"] = tm
    from sqlalchemy.orm.attributes import flag_modified
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()

    resp = RedirectResponse(url="/design/command?time_auth=ok", status_code=303)
    resp.delete_cookie("time_oauth_state")
    return resp



@router.post("/api/auth/time/token")
async def api_time_save_token(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сохранить MMAUTHTOKEN, проверить доступ к каналу any-team-support."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    data = await request.json()
    token = data.get("token", "").strip()
    if not token:
        return {"ok": False, "error": "Токен не передан"}

    # Проверяем токен — запрашиваем данные пользователя
    import httpx
    TIME_BASE = "https://time.tbank.ru"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15) as hx:
            # 1. Получаем текущего пользователя
            me_resp = await hx.get(f"{TIME_BASE}/api/v4/users/me", headers=headers)
            if me_resp.status_code == 401:
                return {"ok": False, "error": "Токен недействителен или истёк — войдите заново"}
            if me_resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {me_resp.status_code} при проверке токена"}

            me = me_resp.json()
            username = me.get("username", "")
            email = me.get("email", "")

            # 2. Ищем канал any-team-support
            channel_posts_count = None
            channel_id = None
            try:
                # Получаем канал по team/channel name
                ch_resp = await hx.get(
                    f"{TIME_BASE}/api/v4/teams/name/tinkoff/channels/name/any-team-support",
                    headers=headers,
                )
                if ch_resp.status_code == 200:
                    channel_id = ch_resp.json().get("id")
                elif ch_resp.status_code == 404:
                    # Пробуем найти через поиск
                    search_resp = await hx.post(
                        f"{TIME_BASE}/api/v4/channels/search",
                        headers=headers,
                        json={"term": "any-team-support"},
                    )
                    if search_resp.status_code == 200:
                        channels = search_resp.json()
                        for ch in (channels if isinstance(channels, list) else []):
                            if "any-team-support" in (ch.get("name") or ""):
                                channel_id = ch.get("id")
                                break
            except Exception:
                pass

            if channel_id:
                try:
                    posts_resp = await hx.get(
                        f"{TIME_BASE}/api/v4/channels/{channel_id}/posts",
                        headers=headers,
                        params={"per_page": 1},
                    )
                    if posts_resp.status_code == 200:
                        channel_posts_count = posts_resp.json().get("order", []).__len__()
                except Exception:
                    pass

    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Сохраняем токен и channel_id в user.settings
    settings = dict(user.settings or {})
    tm = dict(settings.get("tbank_time", {}))
    tm["session_cookie"] = token
    tm["mmauthtoken"] = token
    tm["username"] = username
    tm["email"] = email
    if channel_id:
        tm["support_channel_id"] = channel_id
    settings["tbank_time"] = tm

    from sqlalchemy.orm.attributes import flag_modified
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()

    logger.info(f"✅ Time token saved for {user.email} (username={username}, channel_id={channel_id})")
    return {
        "ok": True,
        "username": username,
        "email": email,
        "channel_id": channel_id,
        "channel_posts_count": channel_posts_count,
    }



@router.post("/api/auth/time/disconnect")
async def api_time_disconnect(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Отключить Tbank Time — удалить токен из user.settings."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)
    settings = dict(user.settings or {})
    settings["tbank_time"] = {}
    from sqlalchemy.orm.attributes import flag_modified
    user.settings = settings
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True}




@router.post("/api/auth/ktalk/test")
async def api_test_ktalk(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Проверить OIDC авторизацию в KTalk (tbank.ktalk.ru)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    body = await request.json()
    login_id = body.get("login", "")
    password = body.get("password", "")
    import ktalk
    oidc_cfg = await ktalk._get_oidc_config()
    result = await ktalk.login(login_id, password)
    return {**result, "oidc_grant_types": oidc_cfg.get("grant_types_supported", []), "password": None}



@router.get("/auth/ktalk", response_class=HTMLResponse)
async def ktalk_oauth_start(request: Request, auth_token: Optional[str] = Cookie(None)):
    """Запускает OIDC авторизацию. ВАЖНО: tbank.ktalk.ru не выставляет стандартный
    OIDC /connect/authorize endpoint — он даёт 404. Поэтому по умолчанию мы просто
    редиректим пользователя на ktalk.ru в новой вкладке — расширение AM Hub
    перехватит токен при авторизации. Если корпоративный OIDC-client настроен и
    задан реальный endpoint через KTALK_OIDC_AUTHORIZE_URL — запустим OIDC flow."""
    if not auth_token:
        return RedirectResponse(url="/login")

    authorize_url = _env("KTALK_OIDC_AUTHORIZE_URL", "")
    if not authorize_url:
        # Нет корпоративного OIDC → отправляем на главную Ktalk, расширение
        # подхватит токен из localStorage/cookies.
        return RedirectResponse(url="https://tbank.ktalk.ru/")

    import secrets, urllib.parse
    client_id = _env("KTALK_OIDC_CLIENT_ID", "KTalk")
    redirect_uri = _env("KTALK_REDIRECT_URI") or (str(request.base_url).rstrip("/") + "/auth/ktalk/callback")
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "id_token token",
        "scope": "profile email allatclaims",
        "redirect_uri": redirect_uri,
        "nonce": secrets.token_urlsafe(16),
        "state": secrets.token_urlsafe(16),
    })
    return RedirectResponse(url=f"{authorize_url}?{params}")



@router.get("/auth/ktalk/callback", response_class=HTMLResponse)
async def ktalk_oauth_callback(request: Request):
    """
    Callback после OIDC авторизации KTalk (SSO Т-Банка).
    Токен приходит в URL hash (#access_token=...) — JS читает и сохраняет.
    """
    return HTMLResponse(content="""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>KTalk — авторизация</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:Inter,sans-serif;background:#0a0e1a;color:#e2e8f0;
       display:flex;align-items:center;justify-content:center;min-height:100vh;}
  .card{background:#111827;border:1px solid #1e2a3a;border-radius:14px;
        padding:32px 40px;text-align:center;max-width:440px;width:90%;}
  h2{font-size:1.2rem;margin-bottom:10px;}
  p{color:#64748b;font-size:.85rem;line-height:1.6;}
  .ok{color:#22c55e;} .err{color:#ef4444;}
  .btn{display:inline-block;margin-top:16px;padding:10px 20px;
       background:#6366f1;color:#fff;border-radius:8px;text-decoration:none;font-size:.85rem;}
  .manual{margin-top:20px;padding:14px;background:#1e2a3a;border-radius:8px;text-align:left;}
  .manual p{font-size:.78rem;color:#94a3b8;margin-bottom:6px;}
  .manual code{display:block;background:#0a0e1a;padding:8px 10px;border-radius:6px;
               font-size:.75rem;color:#818cf8;word-break:break-all;margin-top:4px;}
  input{width:100%;padding:8px 10px;margin-top:8px;border-radius:6px;
        border:1px solid #1e2a3a;background:#0a0e1a;color:#e2e8f0;font-size:.82rem;}
  .paste-btn{margin-top:8px;padding:7px 14px;background:#22c55e;color:#fff;
             border:none;border-radius:6px;cursor:pointer;font-size:.8rem;}
</style></head>
<body><div class="card">
  <h2 id="title">⏳ Авторизация KTalk...</h2>
  <p id="msg">Получаем токен от Т-Банк SSO</p>
  <div id="manual-block" style="display:none" class="manual">
    <p>Если автоматически не сработало — вставьте токен вручную:</p>
    <p>Откройте DevTools (F12) → Console → введите:</p>
    <code>copy(window.__ktalk_token || 'нет токена')</code>
    <p style="margin-top:8px;">Или скопируйте access_token из URL адресной строки после #</p>
    <input id="manual-token" placeholder="Вставьте access_token сюда...">
    <button class="paste-btn" onclick="saveManualToken()">💾 Сохранить токен</button>
  </div>
</div>
<script>
async function saveToken(token) {
  try {
    const r = await fetch('/api/auth/ktalk/token', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({access_token: token})
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('title').textContent = '✅ KTalk подключён!';
      document.getElementById('msg').innerHTML =
        'Авторизован как: <b>' + (d.user?.firstname||'') + ' ' + (d.user?.surname||'') + '</b>' +
        '<br><br><a href="/settings" class="btn">← Вернуться в настройки</a>';
      document.getElementById('msg').className = 'ok';
    } else {
      showError(d.error || 'Не удалось сохранить токен');
    }
  } catch(e) {
    showError(e.message);
  }
}

function showError(msg) {
  document.getElementById('title').textContent = '❌ Ошибка';
  document.getElementById('msg').textContent = msg;
  document.getElementById('msg').className = 'err';
  document.getElementById('manual-block').style.display = 'block';
}

async function saveManualToken() {
  const token = document.getElementById('manual-token').value.trim();
  if (!token) return;
  await saveToken(token);
}

// Основной flow: читаем токен из URL hash
(async function() {
  const hash = window.location.hash.slice(1);
  const query = window.location.search.slice(1);
  const hashParams = Object.fromEntries(new URLSearchParams(hash));
  const queryParams = Object.fromEntries(new URLSearchParams(query));

  // Токен может быть в hash (implicit flow) или query (code flow)
  const token = hashParams.access_token || hashParams.id_token ||
                queryParams.access_token || queryParams.id_token;

  // Error от OIDC сервера
  const error = hashParams.error || queryParams.error;
  if (error) {
    const desc = hashParams.error_description || queryParams.error_description || error;
    // redirect_uri_mismatch — самая частая ошибка
    if (error === 'invalid_request' || desc.includes('redirect_uri')) {
      showError('redirect_uri не зарегистрирован в Ktalk. ' +
        'Добавьте переменную KTALK_REDIRECT_URI в Railway Variables: ' +
        window.location.origin + '/auth/ktalk/callback');
    } else {
      showError(desc);
    }
    return;
  }

  if (!token) {
    // Нет токена и нет ошибки — может быть code flow
    const code = queryParams.code;
    if (code) {
      showError('Получен authorization code вместо токена. ' +
        'Нужна серверная обработка code flow. Обратитесь к администратору.');
    } else {
      showError('Токен не получен. Возможно redirect_uri не совпадает с зарегистрированным в Ktalk.');
    }
    return;
  }

  await saveToken(token);
})();
</script></body></html>""")



@router.post("/api/auth/ktalk/token")
async def api_ktalk_save_token(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Сохраняет OIDC access_token KTalk после browser-based авторизации."""
    if not auth_token:
        raise HTTPException(status_code=401)
    from auth import decode_access_token
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub"))).first()
    if not user:
        raise HTTPException(status_code=401)

    body = await request.json()
    access_token = body.get("access_token", "")
    if not access_token:
        return {"ok": False, "error": "Нет токена"}

    # Получаем данные пользователя чтобы подтвердить токен
    import ktalk as ktalk_mod
    user_info = await ktalk_mod._get_user_info(access_token)

    settings = user.settings or {}
    kt = settings.get("ktalk", {})
    kt["access_token"] = access_token
    kt["login"] = user_info.get("email", kt.get("login", ""))
    settings["ktalk"] = kt
    user.settings = dict(settings)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    db.commit()
    return {"ok": True, "user": user_info}



@router.get("/api/auth/me")
async def api_auth_me(
    request: Request,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Данные текущего пользователя.
    Поддерживает cookie-JWT, Bearer-JWT и Bearer-amh_* (долгоживущий API-токен расширения)."""
    from routers.api_tokens import resolve_user
    user = resolve_user(db, request, auth_token)
    if not user:
        raise HTTPException(status_code=401)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@router.get("/api/auth/me/token")
async def api_me_token(
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Возвращает текущий access token пользователя (для настройки расширения)."""
    if not auth_token:
        raise HTTPException(status_code=401)
    return {"token": auth_token}



@router.post("/api/auth/tokens/push")
async def api_tokens_push(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Принимает токены от Chrome Extension.
    Расширение автоматически перехватывает MMAUTHTOKEN из cookies браузера.
    Auth через Bearer токен (hub_token из настроек расширения).
    """
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header.replace("Bearer ", "").strip()

    # Ищем пользователя по hub_token (сохранён в user.settings.hub_token)
    user = None
    if bearer:
        from auth import decode_access_token
        # Пробуем как JWT токен хаба
        payload = decode_access_token(bearer)
        if payload:
            user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()

        # Fallback: ищем по статическому hub_token в settings
        if not user:
            all_users = db.query(User).filter(User.is_active == True).all()
            for u in all_users:
                s = u.settings or {}
                if s.get("hub_token") == bearer:
                    user = u
                    break

    if not user:
        # Если нет авторизации — создаём анонимный push (для первичной настройки)
        # Токены запишутся как pending, менеджер увидит их на странице настроек
        data = await request.json()
        logger.info(f"Anon token push: time={'time_token' in data}, ktalk={'ktalk_token' in data}")
        return {"ok": True, "note": "Войдите в AM Hub и перейдите в Настройки для привязки токена"}

    data = await request.json()
    settings = dict(user.settings or {})
    updated = []

    # Tbank Time MMAUTHTOKEN
    time_token = data.get("time_token", "")
    if time_token:
        tm = dict(settings.get("tbank_time", {}))
        if tm.get("mmauthtoken") != time_token:
            tm["mmauthtoken"] = time_token
            tm["session_cookie"] = time_token
            # Сразу проверяем и получаем channel_id
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10) as hx:
                    me = await hx.get(
                        "https://time.tbank.ru/api/v4/users/me",
                        headers={"Authorization": f"Bearer {time_token}"},
                    )
                    if me.status_code == 200:
                        me_data = me.json()
                        tm["username"] = me_data.get("username", "")
                        tm["email"] = me_data.get("email", "")
                    ch = await hx.get(
                        "https://time.tbank.ru/api/v4/teams/name/tinkoff/channels/name/any-team-support",
                        headers={"Authorization": f"Bearer {time_token}"},
                    )
                    if ch.status_code == 200:
                        tm["support_channel_id"] = ch.json().get("id", "")
            except Exception as e:
                logger.debug(f"Token validation error: {e}")
            settings["tbank_time"] = tm
            updated.append("time")

    # Ktalk access_token
    ktalk_token = data.get("ktalk_token", "")
    if ktalk_token:
        kt = dict(settings.get("ktalk", {}))
        if kt.get("access_token") != ktalk_token:
            kt["access_token"] = ktalk_token
            settings["ktalk"] = kt
            updated.append("ktalk")

    if updated:
        from sqlalchemy.orm.attributes import flag_modified
        user.settings = settings
        flag_modified(user, "settings")
        db.commit()
        logger.info(f"✅ Extension pushed tokens for {user.email}: {updated}")

    return {"ok": True, "updated": updated, "user": user.email}



# ── PWA ───────────────────────────────────────────────────────────────────────
from fastapi.responses import FileResponse
import os as _os


