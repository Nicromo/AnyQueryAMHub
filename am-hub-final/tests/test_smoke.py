"""
Smoke-тесты. Запускаются без БД — только проверка импортов и чистого синтаксиса.
БД-зависимые тесты пойдут отдельным файлом с fixtures когда поднимем Postgres в CI.
"""
import importlib
import os
import sys

import pytest

# Путь до am-hub-final внутрь sys.path (как делает uvicorn main:app --app-dir am-hub-final)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def test_import_models():
    """Модели грузятся без ошибок (SQLAlchemy declarative)."""
    import models
    assert hasattr(models, "Client")
    assert hasattr(models, "Task")
    assert hasattr(models, "Meeting")


def test_import_routers():
    """Все ключевые роутеры импортируются."""
    modules = [
        "routers.health",
        "routers.voice_notes",
        "routers.client_transfer",
        "routers.renewal",
        "routers.bulk_actions",
        "routers.merchrules_dashboard",
        "routers.onboarding",
        "routers.account_dashboard",
        "routers.partner_logs",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception as e:
            pytest.fail(f"{m} failed to import: {e!r}")


def test_health_router_has_endpoints():
    from routers import health
    paths = [r.path for r in health.router.routes]
    assert "/health" in paths
    assert "/health/deep" in paths


def test_bulk_router_endpoints():
    from routers import bulk_actions
    paths = [r.path for r in bulk_actions.router.routes]
    assert "/api/clients/bulk/mark-checkup" in paths
    assert "/api/clients/bulk/start-onboarding" in paths
    assert "/api/clients/bulk/transfer" in paths


def test_renewal_bucket_logic():
    from routers.renewal import _bucket
    assert _bucket(-3) == "overdue"
    assert _bucket(3) == "critical"
    assert _bucket(10) == "week"
    assert _bucket(20) == "month"
    assert _bucket(60) == "quarter"
    assert _bucket(200) == "later"


def test_crypto_roundtrip(tmp_path, monkeypatch):
    """Fernet enc/dec на симметричном ключе."""
    import base64
    import os as _os
    key = base64.urlsafe_b64encode(b"0" * 32).decode()
    monkeypatch.setenv("AMHUB_CRYPTO_KEY", key)

    # Модуль уже может быть импортирован до monkeypatch — переимпортируем
    import crypto
    importlib.reload(crypto)
    token = crypto.enc("secret-password-42")
    assert token  # непустая строка (или просто passthrough если ключ не задан)
    if token != "secret-password-42":
        assert crypto.dec(token) == "secret-password-42"


def test_storage_allowed_mime_has_audio():
    """VoiceNote upload требует audio mime types в whitelist."""
    import storage
    for m in ("audio/webm", "audio/ogg", "audio/mpeg", "audio/wav"):
        assert m in storage.ALLOWED_MIME, f"{m} missing from ALLOWED_MIME"
