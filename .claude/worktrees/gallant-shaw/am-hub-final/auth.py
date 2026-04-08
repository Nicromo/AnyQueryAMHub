"""
Telegram Login Widget авторизация.
Docs: https://core.telegram.org/widgets/login
"""
import hashlib
import hmac
import time
from typing import Optional

from fastapi import Request, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature


def verify_tg_auth(data: dict, bot_token: str) -> bool:
    """Проверяем подпись от Telegram Login Widget."""
    check_hash = data.pop("hash", None)
    if not check_hash:
        return False

    # Строка для проверки — все поля кроме hash, отсортированные
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # Проверяем подпись и свежесть (не старше 1 часа)
    if not hmac.compare_digest(computed, check_hash):
        return False
    if time.time() - int(data.get("auth_date", 0)) > 3600:
        return False
    return True


class SessionManager:
    def __init__(self, secret_key: str):
        self.serializer = URLSafeTimedSerializer(secret_key)

    def create_session(self, tg_id: int, tg_name: str) -> str:
        return self.serializer.dumps({"id": tg_id, "name": tg_name})

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
