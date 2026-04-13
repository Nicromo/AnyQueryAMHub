#!/usr/bin/env python3
"""Один раз отправить тестовый лог встречи (как в примере пользователя). Запуск из корня skills: python3 roadmap-bulk-tasks/test_meeting_log.py"""
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from creds import load_merchrules_creds

# Точные данные из примера пользователя (site_id=221)
PAYLOAD = {
    "site_id": "221",
    "meeting_date": "2026-02-27T11:00:00",
    "summary": "Провели QBR, итоги пришлю в чат",
    "any_planned_actions": "С нашей стороны, подготовим отчёты от уников",
    "partner_planned_actions": "Просим сделать трекинг в апп",
    "recording_link": "https://tbank.ktalk.ru/recordings/Ttmst4QUUHPhJVQGFOuH",
}


def main():
    base_url, login, password = load_merchrules_creds()
    if not base_url or not login or not password:
        print("Ошибка: креды Roadmap не настроены (~/.search-checkup-creds.json → merchrules)")
        return 1
    import requests
    session = requests.Session()
    r = session.post(
        f"{base_url}/backend-v2/auth/login",
        json={"username": login, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Ошибка авторизации: {r.status_code}", r.text[:300])
        return 1
    print("Авторизация OK")
    r_post = session.post(
        f"{base_url}/backend-v2/meetings",
        json=PAYLOAD,
        timeout=30,
    )
    print(f"POST /backend-v2/meetings: {r_post.status_code}")
    if r_post.status_code in (200, 201):
        try:
            body = r_post.json()
            print("Ответ:", body)
        except Exception:
            print(r_post.text[:500])
        print("Лог встречи успешно создан.")
        return 0
    try:
        err = r_post.json()
        msg = err.get("message") or err.get("detail") or err.get("error") or r_post.text
    except Exception:
        msg = r_post.text
    print("Ошибка:", msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
