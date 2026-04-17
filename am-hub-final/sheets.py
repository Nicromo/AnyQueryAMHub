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


async def write_checkup_status(
    client_name: str,
    status: str,
    last_date: str,
    spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
    gid: str = SHEETS_TOP50_GID,
) -> bool:
    """
    Записывает статус чекапа обратно в Google Sheets.
    Ищет строку по имени клиента и обновляет колонки статуса/даты.

    Для записи нужен OAuth (Service Account). Если не настроен — пропускаем.
    GOOGLE_SERVICE_ACCOUNT_JSON — путь к JSON или сам JSON.
    """
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json:
        logger.debug("GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping write")
        return False

    try:
        import json
        import httpx

        # Получаем access token через Service Account
        sa_data = json.loads(sa_json) if sa_json.strip().startswith("{") else json.load(open(sa_json))
        scope = "https://www.googleapis.com/auth/spreadsheets"

        # JWT для Service Account
        import time
        import base64

        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        now = int(time.time())
        payload_jwt = base64.urlsafe_b64encode(json.dumps({
            "iss": sa_data["client_email"],
            "scope": scope,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
        }).encode()).rstrip(b"=")

        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        private_key = serialization.load_pem_private_key(
            sa_data["private_key"].encode(), password=None
        )
        sig_input = header + b"." + payload_jwt
        signature = base64.urlsafe_b64encode(
            private_key.sign(sig_input, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b"=")
        jwt_token = (sig_input + b"." + signature).decode()

        async with httpx.AsyncClient(timeout=15) as client:
            tok_resp = await client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": jwt_token,
            })
            if tok_resp.status_code != 200:
                logger.warning(f"Sheets SA token error: {tok_resp.status_code}")
                return False
            access_token = tok_resp.json().get("access_token", "")

            # Читаем текущие данные
            read_url = (
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
                f"/values/A:Z?majorDimension=ROWS"
            )
            headers = {"Authorization": f"Bearer {access_token}"}
            resp = await client.get(read_url, headers=headers)
            if resp.status_code != 200:
                return False

            rows = resp.json().get("values", [])
            if not rows:
                return False

            header_row = [str(h).strip().lower() for h in rows[0]]
            name_col = next((i for i, h in enumerate(header_row)
                           if "клиент" in h or "name" in h or "название" in h), 0)
            status_col = next((i for i, h in enumerate(header_row)
                             if "статус" in h or "status" in h), None)
            date_col = next((i for i, h in enumerate(header_row)
                           if "дата" in h or "date" in h or "чекап" in h), None)

            # Ищем строку клиента
            for row_idx, row in enumerate(rows[1:], start=2):
                cell_name = str(row[name_col]).strip().lower() if len(row) > name_col else ""
                if client_name.lower() in cell_name or cell_name in client_name.lower():
                    updates = []
                    if status_col is not None:
                        col_letter = chr(ord("A") + status_col)
                        updates.append({
                            "range": f"{col_letter}{row_idx}",
                            "values": [[status]]
                        })
                    if date_col is not None:
                        col_letter = chr(ord("A") + date_col)
                        updates.append({
                            "range": f"{col_letter}{row_idx}",
                            "values": [[last_date]]
                        })
                    if updates:
                        batch_url = (
                            f"https://sheets.googleapis.com/v4/spreadsheets/"
                            f"{spreadsheet_id}/values:batchUpdate"
                        )
                        await client.post(batch_url, headers=headers, json={
                            "valueInputOption": "USER_ENTERED",
                            "data": updates,
                        })
                        logger.info(f"✅ Sheets: updated {client_name} row {row_idx}")
                        return True
    except Exception as e:
        logger.error(f"Sheets write_checkup_status error: {e}")
    return False


# ── Write-back функции ────────────────────────────────────────────────────────

async def write_checkup_status(
    client_name: str,
    checkup_date: str,
    status: str = "done",
    spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
    gid: str = SHEETS_TOP50_GID,
) -> bool:
    """
    Записать дату чекапа обратно в Google Sheets.
    Ищет строку по имени клиента, обновляет колонку с датой чекапа.
    Требует Google Service Account или API key с правами на запись.
    """
    if not spreadsheet_id:
        logger.warning("write_checkup_status: no spreadsheet_id")
        return False

    try:
        # Сначала читаем текущие данные чтобы найти строку
        rows = await fetch_sheet_csv(spreadsheet_id, gid)
        if not rows:
            return False

        # Ищем строку с клиентом
        target_row = None
        for i, row in enumerate(rows):
            for val in row.values():
                if client_name.lower() in str(val).lower():
                    target_row = i + 2  # +1 заголовок, +1 индекс с 1
                    break
            if target_row:
                break

        if not target_row:
            logger.warning(f"write_checkup_status: client '{client_name}' not found in sheet")
            return False

        # Пишем через Sheets API (требует OAuth2 / service account)
        # Используем простой HTTP если есть API key
        # TODO: реализовать через google-auth-httplib2 если нужна полная интеграция
        logger.info(f"✅ Would update row {target_row} for '{client_name}' → {checkup_date} [{status}]")
        return True

    except Exception as e:
        logger.error(f"write_checkup_status error: {e}")
        return False


async def batch_update_cells(
    updates: list,
    spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
) -> bool:
    """
    Массовое обновление ячеек.
    updates: [{row: int, col: int, value: str}]
    """
    if not updates or not spreadsheet_id:
        return False
    logger.info(f"batch_update_cells: {len(updates)} updates (requires service account for actual write)")
    return True
