"""Centralized env helpers — resolve multiple env var aliases to one value.

Использование:
    from env_helpers import tg_bot_token, groq_api_key

    token = tg_bot_token()  # TG_BOT_TOKEN или TELEGRAM_BOT_TOKEN
    key = groq_api_key()    # GROQ_API_KEY или API_GROQ
"""
import os
from typing import Optional


def _first(*keys: str) -> Optional[str]:
    """Возвращает значение первой найденной env-переменной."""
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return None


def tg_bot_token() -> Optional[str]:
    """Telegram Bot API token — TG_BOT_TOKEN или TELEGRAM_BOT_TOKEN."""
    return _first("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")


def tg_notify_chat_id() -> Optional[str]:
    """Telegram chat для уведомлений."""
    return _first("TG_NOTIFY_CHAT_ID", "TELEGRAM_NOTIFY_CHAT_ID")


def groq_api_key() -> Optional[str]:
    """Groq AI API key — GROQ_API_KEY или API_GROQ."""
    return _first("GROQ_API_KEY", "API_GROQ")


def qwen_api_key() -> Optional[str]:
    """Qwen AI API key."""
    return _first("QWEN_API_KEY", "API_QWEN")


def hub_url() -> str:
    """Публичный URL хаба (для OAuth redirects, extension download link)."""
    return (
        os.environ.get("HUB_URL")
        or os.environ.get("APP_URL")
        or "https://anyqueryamhub-production-9654.up.railway.app"
    ).rstrip("/")
