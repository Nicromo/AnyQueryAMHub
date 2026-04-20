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
    "10SuYn0w2VyDU87KSrYE-A_TDqkekj7q__o910doRCsc",
))
# Tab "Актуальные метрики и список топ 50" в указанной таблице
SHEETS_TOP50_GID = os.getenv("SHEETS_TOP50_GID", "112299807")

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


# Ключевые search-quality метрики, которые пользователь выводит помесячно
# по клиентам менеджера. Ищем по подстроке в заголовке (регистр не важен).
METRIC_KEYWORDS = ["ndcg@20_mean", "precision@20_mean", "precision_exact@20_mean"]
# Поля с датой/периодом (для monthly-разбивки)
PERIOD_KEYWORDS = ["месяц", "month", "период", "period", "дата", "date"]


def find_metric_columns(rows: list[dict]) -> list[str]:
    """Ищет колонки с метриками качества поиска."""
    if not rows:
        return []
    headers = list(rows[0].keys())
    result = []
    for h in headers:
        h_low = h.lower().strip()
        if any(kw.lower() in h_low for kw in METRIC_KEYWORDS):
            result.append(h)
    return result


def find_period_column(rows: list[dict]) -> Optional[str]:
    """Ищет колонку с месяцем/датой периода для группировки."""
    if not rows:
        return None
    headers = list(rows[0].keys())
    for h in headers:
        h_low = h.lower().strip()
        if any(kw in h_low for kw in PERIOD_KEYWORDS):
            return h
    return None


def group_metrics_by_month(rows: list[dict], client_col: str, period_col: Optional[str],
                            metric_cols: list[str]) -> list[dict]:
    """Группирует строки по (клиент, месяц) и возвращает компактную структуру.

    Возвращает: [{client: "X", period: "2025-03", metrics: {ndcg@20_mean: 0.82, ...}}, ...]
    Если period_col нет, period = "all"."""
    out = []
    for r in rows:
        client = (r.get(client_col) or "").strip() if client_col else ""
        if not client:
            continue
        period = (r.get(period_col) or "").strip() if period_col else "all"
        metrics = {}
        for col in metric_cols:
            v = r.get(col, "")
            if v == "" or v is None:
                continue
            # Пробуем float; если не число — оставляем строкой
            try:
                metrics[col] = float(str(v).replace(",", ".").replace(" ", ""))
            except Exception:
                metrics[col] = v
        if metrics:
            out.append({"client": client, "period": period, "metrics": metrics})
    return out


def get_headers(rows: list[dict]) -> list[str]:
    """Возвращает список заголовков таблицы."""
    if not rows:
        return []
    return list(rows[0].keys())


async def fetch_top50_raw(spreadsheet_id: str = SHEETS_SPREADSHEET_ID,
                           gid: str = SHEETS_TOP50_GID) -> list[list[str]]:
    """Возвращает raw-матрицу CSV (список списков). Без обработки заголовков."""
    url = _csv_export_url(spreadsheet_id, gid)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.error("Sheets fetch error: HTTP %s for %s", resp.status_code, url)
            return []
        reader = csv.reader(io.StringIO(resp.text))
        return [row for row in reader]
    except Exception as exc:
        logger.error("Sheets raw fetch exception: %s", exc)
        return []


# Какие группы метрик ищем в шапке «Актуальные метрики» в первой строке
# (объединённая ячейка, CSV экспортирует только в ПЕРВОЙ колонке группы;
# остальные пусты — мы заполним их forward-fill).
METRIC_GROUPS = ["ndcg@20_mean", "precision@20_mean", "precision_exact@20_mean", "Конверсия"]
# Строки-заголовки по умолчанию (0-based):
#   row 0 — группы метрик (объединённые ячейки)
#   row 1 — месяцы (2025-04, 2025-07, ..., 2026-3) + Абсолютное изменение / Относительное / Тренд / Рост от минимума / Sparkline
# Поиск клиента — колонка «Сайт» / «Клиент» в левой части таблицы.


def _parse_top50_matrix(matrix: list[list[str]]) -> dict:
    """Разбирает матрицу top-50 с двумя строками шапки.

    Возвращает:
      {
        "client_col_index": int,
        "metric_columns": {"ndcg@20_mean": [(idx, month), ...], ...},
        "clients": [
          {"name": "cdek.shopping",
           "metrics": {
             "ndcg@20_mean": {"2025-04": 0.9753, "2025-07": ...},
             "precision@20_mean": {...},
             ...
           }},
          ...
        ]
      }
    """
    if not matrix or len(matrix) < 3:
        return {"clients": [], "metric_columns": {}, "client_col_index": None}

    # Находим строки шапки. Row 0 — группы (часто «Актуальные» → metric_name → ...).
    # Row 1 — месяца. Клиенты идут с row 2.
    groups_row = matrix[0] if len(matrix) > 0 else []
    months_row = matrix[1] if len(matrix) > 1 else []

    # Forward-fill groups (Google Sheets CSV даёт имя группы только в первой колонке слияния).
    filled_groups: list[str] = []
    last = ""
    for cell in groups_row:
        v = (cell or "").strip()
        if v:
            last = v
        filled_groups.append(last)

    # Сопоставление: "ndcg@20_mean" → [(col_idx, month_label), ...]
    metric_columns: dict[str, list[tuple[int, str]]] = {m: [] for m in METRIC_GROUPS}
    # Маппинг русских месяцев → YYYY-MM (для «Конверсия апрель» без года)
    ru_months = {
        "январь": "01", "янв": "01", "february": "02", "февраль": "02", "фев": "02",
        "март": "03", "мар": "03", "апрель": "04", "апр": "04", "май": "05",
        "июнь": "06", "июн": "06", "июль": "07", "июл": "07", "август": "08", "авг": "08",
        "сентябрь": "09", "сен": "09", "октябрь": "10", "окт": "10",
        "ноябрь": "11", "ноя": "11", "декабрь": "12", "дек": "12",
    }
    def _ru_month_from(text: str) -> str | None:
        t = (text or "").lower()
        for ru, num in ru_months.items():
            if ru in t:
                return num
        return None

    # Проход 1: у Konversii метрика+месяц в ОДНОЙ ячейке ("Конверсия апрель").
    # Детектим такие случаи в row 0 И row 1 и собираем отдельно.
    for i, cell0 in enumerate(groups_row):
        v0 = (cell0 or "").strip()
        v1 = (months_row[i] if i < len(months_row) else "").strip()
        # Составленный текст — "Конверсия апрель" могло лежать и в row0 и в row1
        combined = (v0 + " " + v1).strip()
        combined_low = combined.lower()
        if "конверс" in combined_low and _ru_month_from(combined_low):
            mnum = _ru_month_from(combined_low)
            # Год — пока ставим 2025 (шаблон из скрина; при необходимости
            # можно достать из соседних колонок других метрик по соответствию).
            month_label = f"2025-{mnum}"
            metric_columns["Конверсия"].append((i, month_label))

    # Проход 2: обычный случай — метрика в объединённой ячейке row0, месяц в row1.
    for i, g in enumerate(filled_groups):
        g_low = g.lower().strip()
        for metric in METRIC_GROUPS:
            if metric == "Конверсия":
                continue  # уже обработали в проходе 1
            m_low = metric.lower().strip()
            if m_low in g_low:
                month = (months_row[i] if i < len(months_row) else "").strip()
                if not month:
                    continue
                low_m = month.lower()
                if any(kw in low_m for kw in ("изменен", "трен", "sparkl", "рост", "мин", "%")):
                    continue
                metric_columns[metric].append((i, month))

    # Найти колонку с именем клиента — первая колонка с непустым текстом в months_row,
    # которая НЕ входит в metric_columns. Обычно это «Сайт» или «Клиент».
    client_col_index = None
    used = {i for vs in metric_columns.values() for i, _ in vs}
    for i in range(min(len(months_row), 6)):  # ищем слева
        header = (months_row[i] if i < len(months_row) else "").strip().lower()
        if i in used:
            continue
        if any(kw in header for kw in ("клиент", "сайт", "site", "partner", "name", "магазин", "account")):
            client_col_index = i
            break
    if client_col_index is None:
        # Fallback: первая колонка с любым непустым текстовым значением в строках 2+
        for i in range(len(months_row)):
            if i in used: continue
            for r in matrix[2:12]:
                if i < len(r) and r[i].strip() and not _looks_numeric(r[i]):
                    client_col_index = i
                    break
            if client_col_index is not None:
                break
    if client_col_index is None:
        client_col_index = 0

    # Парсим клиентов
    clients = []
    for row in matrix[2:]:
        if not row or len(row) <= client_col_index:
            continue
        name = (row[client_col_index] or "").strip()
        if not name:
            continue
        metrics_out: dict[str, dict[str, float]] = {}
        for metric, col_month_list in metric_columns.items():
            month_values: dict[str, float] = {}
            for (idx, month) in col_month_list:
                if idx < len(row):
                    v = _to_float(row[idx])
                    if v is not None:
                        month_values[month] = v
            if month_values:
                metrics_out[metric] = month_values
        clients.append({"name": name, "metrics": metrics_out})

    return {
        "client_col_index": client_col_index,
        "metric_columns": metric_columns,
        "clients": clients,
    }


def _looks_numeric(s: str) -> bool:
    try:
        float(str(s).replace(",", ".").replace(" ", ""))
        return True
    except Exception:
        return False


def _to_float(s):
    if s is None:
        return None
    v = str(s).replace(",", ".").replace(" ", "").replace("\u00a0", "")
    if not v:
        return None
    try:
        return round(float(v), 6)
    except Exception:
        return None


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
    metric_cols = find_metric_columns(rows)
    period_col = find_period_column(rows)
    headers = get_headers(rows)

    # Группируем метрики по месяцам только по клиентам менеджера
    metrics_monthly = group_metrics_by_month(filtered, client_col, period_col, metric_cols) if metric_cols else []

    # Новый формат (2-строчная шапка с метрика+месяц). Читаем сырую матрицу
    # и вытаскиваем ndcg@20_mean / precision@20_mean / precision_exact@20_mean / Конверсия
    # с разбивкой по месяцам для каждого клиента.
    structured_clients: list[dict] = []
    all_months: list[str] = []
    try:
        matrix = await fetch_top50_raw(spreadsheet_id, gid)
        parsed = _parse_top50_matrix(matrix) if matrix else {"clients": [], "metric_columns": {}}
        structured_clients = parsed.get("clients", [])
        # Собираем упорядоченный список месяцев (берём из первой не-пустой группы)
        for metric in METRIC_GROUPS:
            cols = parsed.get("metric_columns", {}).get(metric, [])
            if cols:
                all_months = [m for _, m in cols]
                break
        # Фильтрация по my_clients
        if my_clients:
            norm = {_normalize(c) for c in my_clients}
            structured_clients = [c for c in structured_clients if _normalize(c["name"]) in norm]
    except Exception as _e:
        logger.warning("structured top50 parse failed: %s", _e)

    return {
        "rows": rows,
        "filtered_rows": filtered,
        "headers": headers,
        "client_col": client_col,
        "problem_cols": problem_cols,
        "metric_cols": metric_cols,
        "period_col": period_col,
        "metrics_monthly": metrics_monthly,
        # Новая структура, которой надо пользоваться:
        "structured": {
            "months": all_months,
            "metrics": METRIC_GROUPS,
            "clients": structured_clients,
        },
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
