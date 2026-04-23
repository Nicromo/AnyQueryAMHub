"""
AM Hub Integrations Module

Доступные интеграции:
    - airtable: Получение клиентов, менеджеров, обновление встреч
    - merchrules_extended: Расширенная интеграция с Merchrules API
    - ktalk: Встречи и транскрипции из Tbank Ktalk
    - tbank_time: Обращения в саппорт из Tbank Time
    - dashboard: Двусторонняя синхронизация с дашбордом
    - google_drive: Google Drive (OAuth2, чтение файлов)
    - jira_api: Jira REST API v3 (задачи, проекты, комментарии)
"""

from . import airtable
from . import merchrules_extended as merchrules
from . import ktalk
from . import tbank_time
from . import dashboard
from . import google_drive
from . import jira_api

__all__ = [
    "airtable",
    "merchrules",
    "ktalk",
    "tbank_time",
    "dashboard",
    "google_drive",
    "jira_api",
]
