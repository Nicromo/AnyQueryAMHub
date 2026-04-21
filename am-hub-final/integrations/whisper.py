"""
whisper.py — транскрипция аудио через Groq Whisper-large-v3 (бесплатный tier).

Env:
  GROQ_API_KEY — берётся либо из env, либо из user.settings.groq.api_key.

Используется для voice_notes.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3")


async def transcribe_bytes(
    audio: bytes,
    filename: str = "audio.webm",
    api_key: Optional[str] = None,
    language: str = "ru",
) -> Optional[str]:
    """Возвращает распознанный текст или None при ошибке."""
    key = api_key or os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        logger.warning("transcribe_bytes: GROQ_API_KEY not configured")
        return None
    if not audio:
        return None
    try:
        async with httpx.AsyncClient(timeout=120) as hx:
            r = await hx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, "audio/webm")},
                data={"model": DEFAULT_MODEL, "language": language,
                      "response_format": "json"},
            )
        if r.status_code != 200:
            logger.warning(f"whisper transcribe http {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        return (body.get("text") or "").strip()
    except Exception as e:
        logger.error(f"whisper transcribe failed: {e}")
        return None
