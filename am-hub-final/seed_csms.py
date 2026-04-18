"""
seed_csms.py — добавляет CSM-менеджеров в БД (идемпотентно).

Запуск:
    python seed_csms.py             # из корня am-hub-final, DATABASE_URL из env
    python seed_csms.py --dry-run   # показать что будет создано без записи

Также вызывается через endpoint POST /admin/seed-csms (admin only).
"""
from __future__ import annotations

import sys
from typing import List, Tuple

# Формат: (email, first_name, last_name)
# first_name хранит инициалы с точками — "Д.", "А.А.", "Т.А." —
# чтобы @property User.name собирал "Д. Архангельский" / "Т.А. Андрианова".
CSMS: List[Tuple[str, str, str]] = [
    ("d.arkhangelskiy@tbank.ru",  "Д.",   "Архангельский"),
    ("y.bandero@tbank.ru",        "Я.",   "Бандеро"),
    ("e.kryakhova@tbank.ru",      "Е.",   "Кряхова"),
    ("k.pengrin@tbank.ru",        "К.",   "Пенгрин"),
    ("a.gayfullina@tbank.ru",     "А.",   "Гайфуллина"),
    ("a.a.ganeeva@tbank.ru",      "А.А.", "Ганеева"),
    ("ni.shmelev@tbank.ru",       "Н.",   "Шмелев"),
    ("a.a.koshkareva@tbank.ru",   "А.А.", "Кошкарева"),
    ("niki.medvedev@tbank.ru",    "Н.",   "Медведев"),
    ("k.a.demidova@tbank.ru",     "К.А.", "Демидова"),
    ("s.shkapa@tbank.ru",         "С.",   "Шкапа"),
    ("t.o.lukyanova@tbank.ru",    "Т.О.", "Лукьянова"),
    ("n.i.zaporozhets@tbank.ru",  "Н.И.", "Запорожец"),
    ("ta.a.andrianova@tbank.ru",  "Т.А.", "Андрианова"),
    ("e.ilyinskaya@tbank.ru",     "Е.",   "Ильинская"),
    ("a.v.kraynova@tbank.ru",     "А.В.", "Крайнова"),
    ("y.alik@tbank.ru",           "Я.",   "Алик"),
]


def seed(dry_run: bool = False) -> dict:
    """Идемпотентно добавляет CSM в БД. Возвращает отчёт."""
    from database import SessionLocal
    from models import User

    created, existed = [], []
    with SessionLocal() as db:
        for email, first, last in CSMS:
            u = db.query(User).filter(User.email == email).first()
            if u:
                existed.append(email)
                continue
            if dry_run:
                created.append(email)
                continue
            db.add(User(
                email=email,
                first_name=first,
                last_name=last,
                role="manager",
                is_active=True,
                settings={},
            ))
            created.append(email)
        if not dry_run:
            db.commit()

    return {"created": created, "existed": existed, "total": len(CSMS)}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    report = seed(dry_run=dry)
    print(f"{'DRY RUN ' if dry else ''}Seed CSMs — total: {report['total']}")
    print(f"  создано:    {len(report['created'])}")
    print(f"  уже было:   {len(report['existed'])}")
    if report["created"]:
        print("  +" + "\n  +".join(report["created"]))
