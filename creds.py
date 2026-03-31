"""Загрузка/сохранение кредов merchrules из ~/.search-checkup-creds.json (в т.ч. зашифрованный формат)."""
import base64
import hashlib
import json
from pathlib import Path

CREDS_PATH = Path.home() / ".search-checkup-creds.json"
MERCHRULES_BASE_URL = "https://merchrules-qa.any-platform.ru"
# API копирования задач (из доки): другой хост, лимит 10 запросов/мин
COPY_API_BASE_URL = "https://api.merchrules-qa.any-platform.ru"


def load_merchrules_creds():
    """Возвращает (base_url, login, password) или (None, None, None) если нет кредов."""
    if not CREDS_PATH.exists():
        return None, None, None
    raw = CREDS_PATH.read_bytes()
    data = None
    try:
        from cryptography.fernet import Fernet
        import getpass
        key_material = hashlib.sha256(
            (str(CREDS_PATH) + getpass.getuser() + "search-checkup-creds-v1").encode()
        ).digest()
        key = base64.urlsafe_b64encode(key_material)
        f = Fernet(key)
        dec = f.decrypt(raw)
        data = json.loads(dec.decode("utf-8"))
    except Exception:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, None, None
    mr = (data or {}).get("merchrules") or {}
    url = (mr.get("url") or MERCHRULES_BASE_URL).rstrip("/")
    login = mr.get("login")
    password = mr.get("password")
    if not login or not password:
        return None, None, None
    return url, login, password


def load_grok_api_key() -> str:
    """Загрузить Grok API key из ~/.search-checkup-creds.json."""
    if not CREDS_PATH.exists():
        return ""
    raw = CREDS_PATH.read_bytes()
    data = None
    try:
        from cryptography.fernet import Fernet
        import getpass
        key_material = hashlib.sha256(
            (str(CREDS_PATH) + getpass.getuser() + "search-checkup-creds-v1").encode()
        ).digest()
        key = base64.urlsafe_b64encode(key_material)
        f = Fernet(key)
        dec = f.decrypt(raw)
        data = json.loads(dec.decode("utf-8"))
    except Exception:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return ""
    return (data or {}).get("grok_api_key") or ""


def save_grok_api_key(api_key: str) -> None:
    """Сохранить Grok API key в ~/.search-checkup-creds.json."""
    data = {}
    if CREDS_PATH.exists():
        try:
            raw = CREDS_PATH.read_bytes()
            try:
                from cryptography.fernet import Fernet
                import getpass
                key_material = hashlib.sha256(
                    (str(CREDS_PATH) + getpass.getuser() + "search-checkup-creds-v1").encode()
                ).digest()
                key = base64.urlsafe_b64encode(key_material)
                f = Fernet(key)
                dec = f.decrypt(raw)
                data = json.loads(dec.decode("utf-8"))
            except Exception:
                data = json.loads(raw.decode("utf-8"))
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    data["grok_api_key"] = (api_key or "").strip()
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_merchrules_creds(login: str, password: str = "") -> None:
    """Сохранить логин и пароль в ~/.search-checkup-creds.json (URL всегда один).
    Если password пустой и уже есть сохранённый — не перезаписываем."""
    data = {}
    if CREDS_PATH.exists():
        try:
            raw = CREDS_PATH.read_bytes()
            try:
                from cryptography.fernet import Fernet
                import getpass
                key_material = hashlib.sha256(
                    (str(CREDS_PATH) + getpass.getuser() + "search-checkup-creds-v1").encode()
                ).digest()
                key = base64.urlsafe_b64encode(key_material)
                f = Fernet(key)
                dec = f.decrypt(raw)
                data = json.loads(dec.decode("utf-8"))
            except Exception:
                data = json.loads(raw.decode("utf-8"))
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    existing = data.get("merchrules") or {}
    new_password = (password or "").strip()
    if not new_password and existing.get("password"):
        new_password = existing["password"]
    data["merchrules"] = {
        "url": MERCHRULES_BASE_URL,
        "login": (login or "").strip(),
        "password": new_password,
    }
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
