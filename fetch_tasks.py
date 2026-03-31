#!/usr/bin/env python3
"""
Достать задачи партнёра из API Roadmap (merchrules).
Креды: ~/.search-checkup-creds.json → merchrules.

Пример:
  python3 fetch_tasks.py 221
  python3 fetch_tasks.py 221 --csv  # вывести CSV (для tasks/221.csv)
  python3 fetch_tasks.py 221 --json
  из корня skills: python3 roadmap-bulk-tasks/fetch_tasks.py 221 --csv
"""
import argparse
import csv
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import requests
from creds import load_merchrules_creds


def main():
    ap = argparse.ArgumentParser(description="Получить задачи партнёра из Roadmap API")
    ap.add_argument("site_id", help="Site ID партнёра (например 221)")
    ap.add_argument("--csv", action="store_true", help="Вывести CSV (заголовки как в import)")
    ap.add_argument("--json", action="store_true", help="Вывести сырой JSON ответа")
    ap.add_argument("--out", default=None, help="Путь к файлу (CSV или JSON)")
    ap.add_argument("--page-size", type=int, default=100, help="Задач на страницу (макс. 100)")
    args = ap.parse_args()

    base_url, login, password = load_merchrules_creds()
    if not base_url or not login or not password:
        print("Ошибка: нет кредов. Добавь merchrules (url, login, password) в ~/.search-checkup-creds.json", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    r = session.post(
        f"{base_url}/backend-v2/auth/login",
        json={"username": login, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Ошибка авторизации {r.status_code}: {r.text[:500]}", file=sys.stderr)
        sys.exit(1)

    site_id = str(args.site_id).strip()
    all_tasks = []
    page = 1
    while True:
        r_list = session.get(
            f"{base_url}/backend-v2/roadmap",
            params={
                "site_id": site_id,
                "page": page,
                "page_size": args.page_size,
                "sort_by": "created_at",
                "sort_order": "desc",
            },
            timeout=30,
        )
        if r_list.status_code != 200:
            print(f"Ошибка API {r_list.status_code}: {r_list.text[:500]}", file=sys.stderr)
            sys.exit(1)
        try:
            body = r_list.json()
        except Exception as e:
            print(f"Ответ не JSON: {e}", file=sys.stderr)
            sys.exit(1)
        tasks = body.get("tasks") or []
        total = body.get("total") or (len(all_tasks) + len(tasks))
        all_tasks.extend(tasks)
        if not tasks or len(all_tasks) >= total:
            break
        page += 1

    if args.json:
        out = json.dumps({"tasks": all_tasks, "total": len(all_tasks)}, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
        else:
            print(out)
        return

    if args.csv:
        # Заголовки как в generate_plugin / extract_context (parse_tasks)
        fieldnames = [
            "id", "title", "description", "status", "priority", "team",
            "task_type", "assignee", "product", "link", "site_id",
            "due_date", "deadline_month", "tags", "estimated_hours",
            "actual_hours", "created_by", "creator_username", "created_at", "updated_at",
        ]
        rows = []
        for t in all_tasks:
            row = {k: (t.get(k) or "") for k in fieldnames}
            row["site_id"] = site_id
            rows.append(row)
        if args.out:
            with open(args.out, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            print(f"Записано {len(rows)} задач в {args.out}", file=sys.stderr)
        else:
            f = sys.stdout
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return

    # По умолчанию — краткий вывод
    print(f"Site ID {site_id}: {len(all_tasks)} задач")
    for t in all_tasks:
        status = t.get("status") or ""
        title = (t.get("title") or "")[:60]
        print(f"  [{status}] {title}")


if __name__ == "__main__":
    main()
