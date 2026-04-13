"""
Centralized Configuration Management
"""

import os
import logging
from typing import Optional
from pydantic import Field, validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    """Application configuration from environment variables"""

    # App
    APP_NAME: str = Field(default="AM Hub", description="Application name")
    APP_VERSION: str = Field(default="1.0.0", description="Application version")
    ENV: str = Field(default="development", description="Environment: development, staging, production")
    DEBUG: bool = Field(default=False, description="Debug mode")
    PORT: int = Field(default=8000, description="Server port")
    HOST: str = Field(default="0.0.0.0", description="Server host")

    # Database
    DATABASE_URL: str = Field(
        default="postgresql://user:password@localhost/amhub_db",
        description="PostgreSQL connection URL"
    )
    DATABASE_POOL_SIZE: int = Field(default=10, description="DB connection pool size")
    DATABASE_MAX_OVERFLOW: int = Field(default=20, description="DB max overflow connections")
    DATABASE_POOL_RECYCLE: int = Field(default=3600, description="DB connection recycle time (seconds)")

    # JWT
    JWT_SECRET_KEY: str = Field(
        default="your-secret-key-change-in-production",
        description="JWT secret key for token signing"
    )
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT algorithm")
    JWT_EXPIRATION_HOURS: int = Field(default=720, description="JWT token expiration (hours)")

    # Email
    EMAIL_PROVIDER: str = Field(default="smtp", description="Email provider: smtp, sendgrid, postmark")
    FROM_EMAIL: str = Field(default="noreply@amhub.local", description="From email address")
    SMTP_HOST: str = Field(default="smtp.gmail.com", description="SMTP host")
    SMTP_PORT: int = Field(default=587, description="SMTP port")
    SMTP_USERNAME: str = Field(default="", description="SMTP username")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password")
    SENDGRID_API_KEY: str = Field(default="", description="SendGrid API key")
    POSTMARK_API_KEY: str = Field(default="", description="Postmark API key")

    # Integrations
    KTALK_BASE_URL: str = Field(default="https://tbank.ktalk.ru", description="Ktalk base URL")
    KTALK_API_TOKEN: str = Field(default="", description="Ktalk API token")
    TIME_BASE_URL: str = Field(default="https://time.tbank.ru", description="Tbank Time base URL")
    TIME_API_TOKEN: str = Field(default="", description="Tbank Time API token")
    AIRTABLE_API_KEY: str = Field(default="", description="Airtable API key")
    AIRTABLE_BASE_ID: str = Field(default="", description="Airtable base ID")
    MERCHRULES_API_KEY: str = Field(default="", description="Merchrules API key")
    MERCHRULES_API_URL: str = Field(default="https://api.merchrules.com", description="Merchrules API URL")
    DASHBOARD_API_URL: str = Field(default="", description="Dashboard API URL")
    DASHBOARD_API_KEY: str = Field(default="", description="Dashboard API key")

    # Groq AI
    GROQ_API_KEY: str = Field(default="", description="Groq API key for AI")
    GROQ_MODEL: str = Field(default="mixtral-8x7b-32768", description="Groq model")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram bot token")
    TELEGRAM_CHAT_ID: str = Field(default="", description="Telegram chat ID for notifications")

    # API
    API_RATE_LIMIT: int = Field(default=100, description="API requests per minute per IP")
    ALLOWED_ORIGINS: str = Field(default="*", description="CORS allowed origins (comma-separated)")

    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @validator("ENV")
    def validate_env(cls, v):
        valid_values = ["development", "staging", "production"]
        if v not in valid_values:
            raise ValueError(f"ENV must be one of {valid_values}")
        return v

    @validator("EMAIL_PROVIDER")
    def validate_email_provider(cls, v):
        valid_values = ["smtp", "sendgrid", "postmark"]
        if v not in valid_values:
            raise ValueError(f"EMAIL_PROVIDER must be one of {valid_values}")
        return v

    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        valid_values = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v not in valid_values:
            raise ValueError(f"LOG_LEVEL must be one of {valid_values}")
        return v

    def get_allowed_origins(self) -> list:
        """Parse ALLOWED_ORIGINS as list"""
        if self.ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]


# Create global settings instance
def get_settings() -> AppSettings:
    """Get application settings"""
    try:
        settings = AppSettings()
        logger.info(f"✅ Configuration loaded for {settings.ENV} environment")
        
        # Validate critical settings
        if settings.ENV == "production":
            if settings.JWT_SECRET_KEY == "your-secret-key-change-in-production":
                raise ValueError("JWT_SECRET_KEY must be changed in production!")
            if not settings.DATABASE_URL or "localhost" in settings.DATABASE_URL:
                raise ValueError("DATABASE_URL must point to production database!")
        
        return settings
    
    except Exception as e:
        logger.error(f"❌ Failed to load configuration: {e}")
        raise


# Global settings instance (lazy loaded)
_settings: Optional[AppSettings] = None


def settings() -> AppSettings:
    """Get or create settings instance"""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def validate_config() -> tuple[bool, str]:
    """
    Validate configuration on startup
    
    Returns:
        (is_valid, message)
    """
    try:
        cfg = settings()
        
        # Check database connection
        from database import SessionLocal, engine
        try:
            with SessionLocal() as db:
                db.execute("SELECT 1")
            logger.info("✅ Database connection OK")
        except Exception as e:
            return False, f"Database connection failed: {e}"
        
        # Check email configuration
        if cfg.EMAIL_PROVIDER == "smtp":
            if not cfg.SMTP_HOST or not cfg.SMTP_USERNAME:
                return False, "SMTP configuration incomplete"
        elif cfg.EMAIL_PROVIDER == "sendgrid":
            if not cfg.SENDGRID_API_KEY:
                return False, "SendGrid API key not configured"
        elif cfg.EMAIL_PROVIDER == "postmark":
            if not cfg.POSTMARK_API_KEY:
                return False, "Postmark API key not configured"
        
        logger.info("✅ Email configuration OK")
        
        # Check integrations (warning only)
        missing_integrations = []
        if not cfg.AIRTABLE_API_KEY:
            missing_integrations.append("Airtable")
        if not cfg.MERCHRULES_API_KEY:
            missing_integrations.append("Merchrules")
        
        if missing_integrations:
            logger.warning(f"⚠️ Optional integrations not configured: {', '.join(missing_integrations)}")
        else:
            logger.info("✅ All integrations configured")
        
        return True, "Configuration valid"
    
    except Exception as e:
        logger.error(f"❌ Configuration validation failed: {e}")
        return False, str(e)


if __name__ == "__main__":
    # Test configuration loading
    cfg = settings()
    print(f"App: {cfg.APP_NAME} v{cfg.APP_VERSION}")
    print(f"Environment: {cfg.ENV}")
    print(f"Database: {cfg.DATABASE_URL[:50]}...")
    print(f"Email Provider: {cfg.EMAIL_PROVIDER}")
    print(f"Log Level: {cfg.LOG_LEVEL}")
    
    # Validate
    valid, msg = validate_config()
    print(f"Validation: {msg}")
