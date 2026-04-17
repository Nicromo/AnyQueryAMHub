"""Sentry инициализация — вызывается из main.py при старте."""
import os, logging

logger = logging.getLogger(__name__)

def init_sentry():
    dsn = os.environ.get("SENTRY_DSN", "")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FastApiIntegration(transaction_style="url"),
                SqlalchemyIntegration(),
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
            traces_sample_rate=0.1,   # 10% транзакций
            profiles_sample_rate=0.05,
            environment=os.environ.get("RAILWAY_ENVIRONMENT", "production"),
            release=os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown")[:8],
            send_default_pii=False,   # не передаём PII
        )
        logger.info("✅ Sentry initialized")
    except Exception as e:
        logger.warning(f"Sentry init failed: {e}")
