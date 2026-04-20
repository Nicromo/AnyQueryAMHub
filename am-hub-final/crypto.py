"""
crypto.py — симметричное шифрование секретов в user.settings.

Ключ берётся из env AMHUB_SECRETS_KEY (Fernet key, 32 url-safe base64 байта).
Если ключ не задан — fallback: plain-text (с warning в логах). В проде AMHUB_SECRETS_KEY обязателен.

Шифруемый формат: "enc::v1::<token>" — чтобы при чтении мы отличали зашифрованное
от старых plain-text значений (legacy) и плавно мигрировали.

Использование:
  from crypto import enc, dec
  settings["merchrules"]["password"] = enc(plain_password)
  plain = dec(settings["merchrules"]["password"])  # работает и для plain, и для enc
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX = "enc::v1::"
_warned = False


def _fernet():
    key = os.environ.get("AMHUB_SECRETS_KEY", "").strip()
    if not key:
        global _warned
        if not _warned:
            logger.warning("AMHUB_SECRETS_KEY not set — secrets stored in plaintext (INSECURE)")
            _warned = True
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        logger.error(f"Fernet init failed: {e}")
        return None


def enc(plain: Optional[str]) -> Optional[str]:
    """Шифрует plain-text. Если ключ не задан — возвращает plain (совместимо со старым кодом)."""
    if plain is None or plain == "":
        return plain
    if isinstance(plain, str) and plain.startswith(_PREFIX):
        return plain  # уже зашифровано
    f = _fernet()
    if not f:
        return plain
    try:
        token = f.encrypt(plain.encode("utf-8")).decode("ascii")
        return _PREFIX + token
    except Exception as e:
        logger.error(f"enc failed: {e}")
        return plain


def dec(stored: Optional[str]) -> Optional[str]:
    """Дешифрует если было зашифровано; иначе возвращает как есть (legacy plain-text)."""
    if stored is None or stored == "":
        return stored
    if not isinstance(stored, str):
        return stored
    if not stored.startswith(_PREFIX):
        return stored  # legacy plain — возвращаем как есть
    f = _fernet()
    if not f:
        logger.warning("Cannot decrypt: AMHUB_SECRETS_KEY not configured")
        return stored  # вернём как есть (с префиксом) — клиент увидит что нет ключа
    try:
        token = stored[len(_PREFIX):]
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.error(f"dec failed: {e}")
        return None


def generate_key() -> str:
    """CLI helper: `python -c 'from crypto import generate_key; print(generate_key())'`."""
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode("ascii")
    except Exception as e:
        return f"ERROR: {e}"
