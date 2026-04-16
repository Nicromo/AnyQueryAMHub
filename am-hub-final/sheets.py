"""
Google Sheets — чтение публичных таблиц через CSV-экспорт.
Не требует авторизации для публично-доступных таблиц.
"""
import csv
import io
import os
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

import re as _re

def _extract_sheets_id(val: str) -> str:
    if not val:
        return ""
    m = _re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", val)
    return m.group(1) if m else val

# Настройки по умолчанию
SHEETS_SPREADSHEET_ID = _extract_sheets_id(os.getenv(
    "SHEETS_SPREADSHEET_ID",
    "1baqs2xGFZNxCuAwTfuDiE52KXIaLaKtuZzcSN4lce3M",
))
SHEETS_TOP50_GID = os.getenv("SHEETS_TOP50_GID", "374545260")

# Список клиентов из БД — для фильтрации строк таблицы.
# Если пусто — возвращаем все строки.
TIMEOUT_S = 15


def _csv_export_url(spreadsheet_id: str, gid: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        f"/export?format=csv&gid={gid}"
    )


async def fetch_sheet_csv(spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
                           gid: str = SHEETS_TOP50_GID) -> list[dict]:
    """
    Скачивает публичный Google Sheet и возвращает список dict (header → value).
    При ошибке возвращает пустой список.
    """
    url = _csv_export_url(spreadsheet_id, gid)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.error("Sheets fetch error: HTTP %s for %s", resp.status_code, url)
            return []
        text = resp.text
    except Exception as exc:
        logger.error("Sheets fetch exception: %s", exc)
        return []

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Убираем пустые строки (все значения пусты)
        if any(v.strip() for v in row.values()):
            rows.append({k.strip(): v.strip() for k, v in row.items() if k})
    return rows


def _normalize(s: str) -> str:
    """Нижний регистр + убираем пробелы для сравнения."""
    return s.lower().strip()


def filter_my_clients(rows: list[dict], my_clients: list[str]) -> list[dict]:
    """
    Фильтрует строки таблицы: оставляет только те,
    где одно из значений совпадает с именем клиента из my_clients.
    """
    if not my_clients:
        return rows
    norm_clients = {_normalize(c) for c in my_clients}
    result = []
    for row in rows:
        for v in row.values():
            if _normalize(v) in norm_clients:
                result.append(row)
                break
    return result


def find_client_column(rows: list[dict]) -> Optional[str]:
    """
    Пытается угадать, какая колонка содержит имя клиента.
    Ищет по ключевым словам в заголовке: 'клиент', 'партнёр', 'site', 'client', 'company', 'partner'.
    """
    if not rows:
        return None
    headers = list(rows[0].keys())
    keywords = ["клиент", "партнёр", "partner", "client", "company", "site", "магазин"]
    for h in headers:
        if any(kw in h.lower() for kw in keywords):
            return h
    # Fallback — первая колонка
    return headers[0] if headers else None


def find_problem_columns(rows: list[dict]) -> list[str]:
    """
    Угадывает колонки с проблемами/запросами клиентов.
    """
    if not rows:
        return []
    headers = list(rows[0].keys())
    keywords = [
        "проблем", "запрос", "задач", "request", "issue", "problem",
        "задани", "не реализ", "пробел", "нельзя", "невозможн",
        "жалоб", "ошибк", "баг", "bug", "complaint", "gap",
        "комментар", "описан", "суть", "тема",
    ]
    result = []
    for h in headers:
        if any(kw in h.lower() for kw in keywords):
            result.append(h)
    return result or []


def get_headers(rows: list[dict]) -> list[str]:
    """Возвращает список заголовков таблицы."""
    if not rows:
        return []
    return list(rows[0].keys())


async def get_top50_data(
    my_clients: list[str],
    spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
    gid: str = SHEETS_TOP50_GID,
) -> dict:
    """
    Главная точка входа для Top-50.
    Возвращает:
      {
        "rows": [...],          # все строки (или только мои клиенты)
        "filtered_rows": [...], # только мои клиенты
        "headers": [...],
        "client_col": "...",
        "problem_cols": [...],
        "fetched_at": "...",
        "error": None | "...",
      }
    """
    rows = await fetch_sheet_csv(spreadsheet_id, gid)
    if not rows:
        return {
            "rows": [],
            "filtered_rows": [],
            "headers": [],
            "client_col": None,
            "problem_cols": [],
            "fetched_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "error": "Не удалось загрузить данные из Google Sheets. "
                     "Проверьте, что таблица открыта для просмотра по ссылке.",
        }

    filtered = filter_my_clients(rows, my_clients)
    client_col = find_client_column(rows)
    problem_cols = find_problem_columns(rows)
    headers = get_headers(rows)

    return {
        "rows": rows,
        "filtered_rows": filtered,
        "headers": headers,
        "client_col": client_col,
        "problem_cols": problem_cols,
        "fetched_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "error": None,
    }
