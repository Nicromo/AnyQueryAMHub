"""
Персональные настройки и креды пользователей.
Храним в ~/.am-hub-users.json — один файл на всех.
"""
import json
import os
from pathlib import Path

CREDS_PATH = Path.home() / ".am-hub-users.json"
MERCHRULES_BASE_URL = os.getenv("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")
COPY_API_BASE_URL = os.getenv("MERCHRULES_COPY_API_URL", "https://api.merchrules.any-platform.ru")


def _read_all() -> dict:
    if not CREDS_PATH.exists():
        return {}
    try:
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_all(data: dict):
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user_settings(email: str) -> dict:
    """Получить настройки конкретного пользователя."""
    all_data = _read_all()
    return (all_data.get("users") or {}).get(email, {})


def save_user_settings(email: str, settings: dict):
    """Сохранить настройки пользователя."""
    all_data = _read_all()
    if "users" not in all_data:
        all_data["users"] = {}
    all_data["users"][email] = settings
    _write_all(all_data)


def get_user_cred(email: str, service: str) -> dict:
    """Получить креды сервиса для конкретного пользователя."""
    user = get_user_settings(email)
    return user.get(service, {})


def save_user_cred(email: str, service: str, cred: dict):
    """Сохранить креды сервиса для пользователя."""
    user = get_user_settings(email)
    user[service] = cred
    save_user_settings(email, user)


def get_user_rules(email: str) -> dict:
    """Получить правила работы менеджера."""
    user = get_user_settings(email)
    return user.get("rules", {
        "min_health_score": 0.5,
        "checkup_interval_days": 30,
        "warning_days": 14,
        "segments": ["ENT", "SME+", "SME-", "SMB", "SS"],
        "auto_create_tasks": True,
        "morning_plan_time": "09:00",
        "weekly_digest_day": "friday",
    })


def get_user_integrations(email: str) -> dict:
    """Получить настройки интеграций пользователя."""
    user = get_user_settings(email)
    return {
        "merchrules": user.get("merchrules", {}),
        "airtable": user.get("airtable", {}),
        "sheets": user.get("sheets", {}),
        "telegram": user.get("telegram", {}),
        "groq": user.get("groq", {}),
        "sendgrid": user.get("sendgrid", {}),
        "ktalk": user.get("ktalk", {}),
        "tbank_time": user.get("tbank_time", {}),
    }


# Legacy compatibility
def load_merchrules_creds():
    return None, None, None


def save_merchrules_creds(login: str, password: str = ""):
    pass


def load_grok_api_key(username=None):
    return ""


def save_grok_api_key(api_key: str, username=None):
    pass


def load_airtable_token(username=None):
    return ""


def save_airtable_token(token: str, username=None):
    pass
