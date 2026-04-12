#!/usr/bin/env python3
"""
Импорт задач из CSV в roadmap для одного site_id.
Лимит API: 5 запросов в 1 час. Между запросами — пауза (по умолчанию 900 сек).

Использование (из корня skills):
  python3 roadmap-bulk-tasks/import_csv_for_site.py roadmap-bulk-tasks/befree_tasks_1967.csv 1967
  python3 roadmap-bulk-tasks/import_csv_for_site.py roadmap-bulk-tasks/befree_tasks_1967.csv 1967 --delay 720
  python3 roadmap-bulk-tasks/import_csv_for_site.py roadmap-bulk-tasks/befree_tasks_1967.csv 1967 --dry-run
"""
import argparse
import csv
import io
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from import_one_task import load_merchrules_creds
from task_defaults import apply_task_defaults

import requests


def csv_cell(s):
    if s is None or s == "":
        return ""
    s = str(s).strip()
    if "," in s or '"' in s or "\n" in s or "\r" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


def build_task_csv_row(fields):
    header = "title,description,status,priority,team,task_type,assignee,product,link,due_date"
    f = apply_task_defaults(fields)
    row = ",".join([
        csv_cell(f.get("title")),
        csv_cell(f.get("description")),
        csv_cell(f.get("status")),
        csv_cell(f.get("priority")),
        csv_cell(f.get("team")),
        csv_cell(f.get("task_type")),
        csv_cell(f.get("assignee")),
        csv_cell(f.get("product")),
        csv_cell(f.get("link")),
        csv_cell(f.get("due_date")),
    ])
    return header + "\n" + row


def main():
    ap = argparse.ArgumentParser(description="Импорт задач из CSV в roadmap для site_id")
    ap.add_argument("csv_path", type=Path, help="Путь к CSV (заголовок: title,description,status,...)")
    ap.add_argument("site_id", help="Site ID партнёра (например 1967)")
    ap.add_argument("--delay", type=int, default=900, help="Пауза между запросами в секундах (по умолчанию 900 = 5/час)")
    ap.add_argument("--dry-run", action="store_true", help="Только вывести задачи, не отправлять")
    args = ap.parse_args()

    csv_path = args.csv_path
    if not csv_path.exists():
        sys.exit(f"Файл не найден: {csv_path}")

    site_id = str(args.site_id).strip()
    base, login, password = load_merchrules_creds()

    # Читаем CSV
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "title" not in (reader.fieldnames or []):
            sys.exit("CSV должен содержать колонку title")
        rows = list(reader)

    tasks = []
    for r in rows:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        tasks.append({
            "title": title,
            "description": (r.get("description") or "").strip(),
            "status": (r.get("status") or "").strip(),
            "priority": (r.get("priority") or "").strip(),
            "team": (r.get("team") or "").strip(),
            "task_type": (r.get("task_type") or "").strip(),
            "assignee": (r.get("assignee") or "").strip(),
            "product": (r.get("product") or "").strip(),
            "link": (r.get("link") or "").strip(),
            "due_date": (r.get("due_date") or "").strip(),
        })

    if not tasks:
        sys.exit("В CSV нет строк с title")

    print(f"Задач к импорту: {len(tasks)} для site_id={site_id}")
    if args.dry_run:
        for i, t in enumerate(tasks, 1):
            print(f"  {i}. {t['title'][:60]}...")
        return

    session = requests.Session()
    r = session.post(f"{base}/backend-v2/auth/login", json={"username": login, "password": password}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Ошибка авторизации {r.status_code}: {r.text[:300]}")

    import_url = f"{base}/backend-v2/import/tasks/csv"
    created_total = 0
    for i, task_fields in enumerate(tasks):
        csv_content = build_task_csv_row(task_fields)
        csv_bytes = csv_content.encode("utf-8")
        files = {"file": ("tasks.csv", io.BytesIO(csv_bytes), "text/csv; charset=utf-8")}
        try:
            r = session.post(import_url, data={"site_id": site_id}, files=files, timeout=60)
        except Exception as e:
            print(f"  [{i+1}] {task_fields['title'][:50]}… — ошибка запроса: {e}", file=sys.stderr)
            continue
        try:
            body = r.json()
        except Exception:
            print(f"  [{i+1}] {task_fields['title'][:50]}… — ответ не JSON: {r.status_code}", file=sys.stderr)
            continue
        if r.status_code == 200:
            created = body.get("created", 0) or 0
            created_total += created
            err_msg = body.get("message") or body.get("detail") or body.get("error")
            if created > 0:
                print(f"  [{i+1}] OK: {task_fields['title'][:55]}…")
            else:
                print(f"  [{i+1}] Не создано: {task_fields['title'][:50]}… — {err_msg or body.get('errors', '')}", file=sys.stderr)
        else:
            err_msg = body.get("message") or body.get("detail") or body.get("error") or r.text[:200]
            print(f"  [{i+1}] HTTP {r.status_code}: {task_fields['title'][:50]}… — {err_msg}", file=sys.stderr)
            if r.status_code == 429:
                print("  Лимит 5 запросов/час. Запустите скрипт позже или с большим --delay.", file=sys.stderr)
                break

        if i < len(tasks) - 1 and args.delay > 0:
            print(f"  Пауза {args.delay} сек…")
            time.sleep(args.delay)

    print(f"\nИтого создано: {created_total} из {len(tasks)}")


if __name__ == "__main__":
    main()
