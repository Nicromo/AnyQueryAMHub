from __future__ import annotations

from typing import Any

import httpx

from backend.config import settings


def exchange_authorization_code(code: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.oauth_client_id,
        "client_secret": settings.oauth_client_secret,
        "code": code,
        "redirect_uri": settings.oauth_redirect_uri,
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            settings.oauth_token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    return r.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.oauth_client_id,
        "client_secret": settings.oauth_client_secret,
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            settings.oauth_token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    return r.json()
