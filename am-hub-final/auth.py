"""
Авторизация через Merchrules.
Пользователь вводит свой логин/пароль от Merchrules Dashboard —
AM Hub проверяет их через MR API и создаёт сессию.
Fallback: если MR не настроен, принимает ADMIN_PASSWORD из env.
"""
import hashlib
import hmac
import os
import time
from typing import Optional

import httpx
from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature

MERCHRULES_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


async def verify_mr_credentials(username: str, password: str) -> Optional[dict]:
    """
    Проверяем логин/пароль через Merchrules API.
    Возвращает dict с данными пользователя или None при ошибке.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MERCHRULES_URL}/backend-v2/auth/login",
                json={"username": username, "password": password},
            )
            if resp.status_code != 200:
                return None

            body = resp.json()
            token = (
                body.get("token")
                or body.get("access_token")
                or body.get("accessToken")
                or ""
            )
            if not token:
                return None

            # Пробуем получить профиль пользователя
            name = username
            role = ""
            mr_user_id = abs(hash(username)) % 10_000_000

            for profile_url in [
                f"{MERCHRULES_URL}/backend-v2/auth/me",
                f"{MERCHRULES_URL}/backend-v2/profile",
                f"{MERCHRULES_URL}/backend-v2/users/me",
            ]:
                try:
                    pr = await client.get(
                        profile_url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=5,
                    )
                    if pr.status_code == 200:
                        p = pr.json()
                        name = (
                            p.get("name") or p.get("full_name") or p.get("fullName")
                            or p.get("first_name") or p.get("firstName")
                            or p.get("display_name") or username
                        )
                        role = p.get("role") or p.get("position") or ""
                        mr_user_id = p.get("id") or mr_user_id
                        break
                except Exception:
                    continue

            return {
                "id":       int(mr_user_id),
                "name":     str(name).strip(),
                "username": username,
                "role":     str(role),
                "mr_token": token,
            }

    except Exception:
        return None


def verify_admin_password(username: str, password: str) -> Optional[dict]:
    """
    Fallback: проверяем против ADMIN_PASSWORD из env.
    Используется если Merchrules не настроен или недоступен.
    """
    if not ADMIN_PASSWORD:
        return None
    if password != ADMIN_PASSWORD:
        return None
    uid = abs(hash(username)) % 10_000_000
    return {
        "id":       uid,
        "name":     username,
        "username": username,
        "role":     "admin",
        "mr_token": "",
    }


class SessionManager:
    def __init__(self, secret_key: str):
        self.serializer = URLSafeTimedSerializer(secret_key)

    def create_session(self, user_data: dict) -> str:
        """Создаём подписанную cookie-сессию с данными пользователя."""
        return self.serializer.dumps(user_data)

    def get_user(self, request: Request) -> Optional[dict]:
        token = request.cookies.get("session")
        if not token:
            return None
        try:
            data = self.serializer.loads(token, max_age=86400 * 7)  # 7 дней
            return data
        except BadSignature:
            return None

    def require_user(self, request: Request) -> dict:
        user = self.get_user(request)
        if not user:
            raise HTTPException(status_code=302, headers={"Location": "/login"})
        return user


# Совместимость: старый интерфейс create_session(tg_id, name)
def _compat_create_session(mgr: SessionManager, tg_id: int, name: str) -> str:
    return mgr.create_session({"id": tg_id, "name": name, "username": str(tg_id), "mr_token": ""})
