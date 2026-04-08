#!/usr/bin/env python3
"""
Тест: создать одну задачу в дорожной карте для партнёра (site_id).
Креды: ~/.search-checkup-creds.json → merchrules (url, login, password).

Пример:
  python3 import_one_task.py 2262 "Протестировать вектора" "Тестовая задача"
  из корня skills: python3 roadmap-bulk-tasks/import_one_task.py 2262 "Протестировать вектора" "Тестовая задача"
"""
import argparse
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

# Чтобы import task_defaults работал при запуске из корня skills
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import requests

CREDS_PATH = Path.home() / ".search-checkup-creds.json"


def load_merchrules_creds():
    if not CREDS_PATH.exists():
        raise SystemExit(f"Файл кредов не найден: {CREDS_PATH}. Добавь ключ merchrules: {{ url, login, password }}.")
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
        except Exception as e:
            raise SystemExit(f"Не удалось прочитать креды: {e}")
    mr = (data or {}).get("merchrules") or {}
    url = (mr.get("url") or "").rstrip("/")
    login = mr.get("login")
    password = mr.get("password")
    if not url or not login or not password:
        raise SystemExit(
            f"В {CREDS_PATH} в ключе merchrules нужны url, login, password. Сейчас: url={bool(url)}, login={bool(login)}, password={bool(password)}."
        )
    return url, login, password


def main():
    ap = argparse.ArgumentParser(description="Создать одну задачу в roadmap для site_id")
    ap.add_argument("site_id", help="Site ID партнёра (например 2262)")
    ap.add_argument("title", nargs="?", default="Протестировать вектора", help="Заголовок задачи")
    ap.add_argument("description", nargs="?", default="Тестовая задача", help="Описание задачи")
    args = ap.parse_args()

    base, login, password = load_merchrules_creds()
    session = requests.Session()

    # Логин
    login_url = f"{base}/backend-v2/auth/login"
    r = session.post(login_url, json={"username": login, "password": password}, timeout=30)
    if r.status_code != 200:
        print(f"Ошибка авторизации {r.status_code}: {r.text[:500]}", file=sys.stderr)
        sys.exit(1)
    print("Авторизация OK")

    from task_defaults import DEFAULT_STATUS, DEFAULT_PRIORITY, DEFAULT_ASSIGNEE

    # CSV: одна строка задачи (title, description; status/priority/assignee — дефолты)
    header = "title,description,status,priority,team,task_type,assignee,product,link,due_date"
    def csv_cell(s):
        if s is None or s == "":
            return ""
        s = str(s)
        if "," in s or '"' in s or "\n" in s:
            return '"' + s.replace('"', '""') + '"'
        return s
    row = ",".join([
        csv_cell(args.title),
        csv_cell(args.description),
        csv_cell(DEFAULT_STATUS),
        csv_cell(DEFAULT_PRIORITY),
        "", "", csv_cell(DEFAULT_ASSIGNEE), "", "", ""
    ])
    csv_content = header + "\n" + row
    csv_bytes = csv_content.encode("utf-8")

    # POST /import/tasks/csv
    import_url = f"{base}/backend-v2/import/tasks/csv"
    site_id = str(args.site_id).strip()
    files = {"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv; charset=utf-8")}
    data = {"site_id": site_id}

    r = session.post(import_url, data=data, files=files, timeout=60)
    print(f"Ответ: {r.status_code}")
    try:
        body = r.json()
        print(json.dumps(body, ensure_ascii=False, indent=2))
        if r.status_code == 200:
            created = body.get("created", 0)
            errors = body.get("errors") or []
            if created > 0 and not errors:
                print(f"\nOK: создана 1 задача для site_id={site_id}")
            elif errors:
                print(f"\nЧастично: создано {created}, ошибки: {errors}", file=sys.stderr)
            else:
                print("\nЗадача не создана (проверь ответ выше).", file=sys.stderr)
        else:
            sys.exit(1)
    except Exception as e:
        print(r.text[:2000])
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
