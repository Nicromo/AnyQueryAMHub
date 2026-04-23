# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Команды разработки

### Запуск сервера

```bash
cd am-hub-final
pip install -r requirements.txt
DATABASE_URL=sqlite:///./dev.db SECRET_KEY=dev-key uvicorn main:app --reload --port 8000
```

Первый запуск с пустой БД требует `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` для создания админа.

### Тесты и линтинг

```bash
# Smoke-тесты (без БД — только импорты и бизнес-логика)
cd am-hub-final
DATABASE_URL="sqlite:///./_ci_smoke.db" SECRET_KEY="test" pytest -q tests/

# Один тест
pytest tests/test_smoke.py::test_import_models -v

# Синтаксическая проверка Python (CI hard gate)
find am-hub-final -name '*.py' -not -path '*/.venv/*' | xargs python -m py_compile

# Линтинг (информационный, не блокирует)
ruff check am-hub-final/ --select E9,F63,F7,F82,F821,F822,F823

# Проверка синтаксиса JS
find am-hub-final/static -name '*.js' -not -path '*/lib/jspdf*' -not -name 'app.js' | xargs -I{} node --check {}
```

### Сборка фронтенда (JSX → bundle)

JSX-исходники в `am-hub-final/static/design/*.jsx` компилируются в `static/design/dist/bundle.js`.

```bash
cd am-hub-final
npm install          # первый раз
npm run build:design # esbuild, ~200мс
git add static/design/dist/bundle.js
```

**bundle.js коммитится в репо** — Railway не запускает npm при деплое.

### Миграции БД

```bash
cd am-hub-final
alembic revision --autogenerate -m "описание изменений"
alembic upgrade head
```

## Архитектура

Репо содержит три продукта:

| Продукт | Путь | Назначение |
|---|---|---|
| **AM Hub** | `am-hub-final/` | Основной FastAPI-сервис |
| **Новый UI (дизайн)** | `am-hub-final/static/design/` + `templates/design/` | JSX-страницы, доступны по `/design/*` параллельно со старым UI |
| **Chrome Extension** | `extension/` | Синхронизация Merchrules ↔ AM Hub |

### Основной сервис (`am-hub-final/`)

```
main.py               — FastAPI app, регистрирует все роутеры, lifespan (init_db + scheduler)
models.py             — SQLAlchemy ORM: Client, Task, Meeting, CheckUp, User, QBR, ...
database.py           — engine / SessionLocal / get_db dependency
auth.py               — JWT-auth, cookies, get_current_user, log_audit
schemas.py            — Pydantic DTO
design_mappers.py     — ORM → dict-конвертеры для нового UI (HEALTH_RISK_MAX и пороги здесь)
scheduler.py          — APScheduler: 11 cron-триггеров (health_drop, nps_low, payment_overdue, ...)
routers/              — 43 модуля, каждый = отдельная часть API
integrations/         — Клиенты к Merchrules, Airtable, KTalk, Whisper, Diginetica
templates/            — Jinja2 (старый UI)
alembic/              — Миграции БД
```

### Ключевые роутеры

- `routers/design.py` — 20 страниц нового UI, конфигурируются через словарь `PAGES`
- `routers/sync.py` — синхронизация с Merchrules и Airtable
- `routers/clients.py` — CRUD клиентов + `/api/clients/{id}/metrics`
- `routers/ai.py` — AI-ассистент с data-grounding (портфель в system prompt)
- `routers/analytics.py` — KPI, heatmap, воронка чекапов
- `routers/auto_tasks.py` — ручной запуск триггеров автозадач

### Паттерны кода

**Auth**: везде используется `user: User = Depends(get_current_user)`. Для страниц admin: `Depends(get_current_admin)`.

**DB сессии**: `db: Session = Depends(get_db)` во всех роутерах.

**Scope switcher**: параметр `scope` (my / group / all) фильтрует данные по `manager_email` или `group_id`. Реализован в большинстве списочных endpoint'ов.

**Автозадачи дедупликация**: задачи хранят `meta.rule_key` + `meta.target_date`. Перед созданием проверяется существующая задача с тем же ключом.

**Health score**: рассчитывается ежедневно в `scheduler.py`. Пороги: `HEALTH_RISK_MAX` (0.55), `HEALTH_WARN_MAX` (0.80) из `design_mappers.py` (переопределяются env-переменными).

### Новый UI (редизайн)

Страницы `/design/*` работают через один шаблон `templates/design/app.html`. Роутер `design.py` читает словарь `PAGES` и рендерит нужный React-компонент из `bundle.js`. Компоненты получают начальные данные через `window.__INITIAL_DATA__` (JSON в шаблоне).

**Добавить новую страницу:**
1. В `routers/design.py` добавить запись в `PAGES`: `"slug": ("ComponentName", ["breadcrumb"], "Заголовок")`
2. Создать `static/design/page_slug.jsx` с функцией + `window.ComponentName = ComponentName`
3. Добавить в `build-design.mjs` массив ORDER
4. `npm run build:design`

### Chrome Extension (`extension/`)

Версионируется отдельно. Упакован как `.crx` с `key.pem` (в `.gitignore`!). Конфигурация endpoint: `/api/extension/config` восстанавливает credentials. Обновления раздаются через `updates.xml` и `/api/extension/download` (динамический ZIP).

## Переменные окружения

### Обязательные для запуска

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | PostgreSQL URL (`postgresql://user:pass@host/db`) или SQLite для dev |
| `SECRET_KEY` | JWT signing key |
| `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` | Создаёт первого админа на пустой БД |

### Интеграции (включаются по наличию переменной)

| Переменная | Интеграция |
|---|---|
| `MERCHRULES_LOGIN`, `MERCHRULES_PASSWORD`, `MERCHRULES_API_URL` | Клиенты, задачи, встречи |
| `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID` | Синхронизация клиентов |
| `AIRTABLE_PAYMENTS_TABLE_ID`, `AIRTABLE_PAYMENTS_VIEW_ID` | Страница оплат |
| `GROQ_API_KEY` / `QWEN_API_KEY` | AI-ассистент (один из) |
| `TG_BOT_TOKEN`, `TG_NOTIFY_CHAT_ID` | Telegram-уведомления |
| `CF_R2_*` | Cloudflare R2 для voice notes (fallback — `/tmp`) |
| `AMHUB_CRYPTO_KEY` | Fernet-ключ для шифрования паролей в `user.settings` |
| `HUB_URL` | Публичный URL хаба (для Chrome extension download URL) |

## CI

GitHub Actions (`.github/workflows/ci.yml`) запускает три задачи на каждый PR и push в main:

1. **lint-python** — `py_compile` как hard gate; `ruff` информационно
2. **check-js** — `node --check` по всем `.js` (кроме jspdf и app.js)
3. **pytest-smoke** — smoke-тесты с `DATABASE_URL=sqlite:///` (без реального Postgres)

Алembic-проверка с реальным Postgres пока отключена (помечена TODO в workflow).
