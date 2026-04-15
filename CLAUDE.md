# AM Hub — правила разработки

## Перед тем как писать код

1. **Если задача непонятна — остановись и спроси.** Не угадывай требования.
2. **Трогай только то, о чём попросили.** Не рефактори соседний код, не добавляй "улучшения" сверху задачи.
3. **Код пиши короткий.** Никакого запаса "на потом", никаких абстракций ради абстракций.
4. **Любой баг-фикс: сначала тест, который падает → потом фикс → тест проходит.**

## Стек

- **Backend**: FastAPI + SQLite (raw sqlite3), Python 3.11+
- **Frontend**: Jinja2 templates, vanilla JS, no build step
- **Deploy**: Railway (env vars через Railway Variables)
- **Интеграции**: Airtable REST API, Merchrules API, Telegram Bot API

## Структура проекта

```
am-hub-final/
  main.py          — FastAPI app, все роуты
  database.py      — SQLite схема + все db-функции
  airtable_sync.py — импорт клиентов из Airtable
  merchrules_sync.py — MR API per-user
  tg_bot.py        — Telegram webhook handler
  templates/       — Jinja2 HTML
  static/          — CSS, JS
```

## Ключевые правила для этого проекта

- Airtable — единственный источник истины для списка клиентов (не хардкодить)
- Поля Airtable по ID: `fldXeHkgIjzvr294Z` (название), `fld0XMiWRh9xzvDy6` (менеджер), `fldreqkwkEXrEGGwg` (site_id), `fldyvNxsQglqiQs48` (сегмент)
- MR credentials хранятся per-user в таблице `manager_credentials` (не шарятся между АМ)
- Сегменты: ENT / SME+ / SME- / SMB / SS (нормализация через SEGMENT_MAP)
- Railway: env vars могут прийти с `<>` вокруг значений — стрипать при парсинге
