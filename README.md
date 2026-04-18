# AM Hub

**Enterprise Account Manager Dashboard** — единая операционная консоль для команды AM: реальные данные из Merchrules, персональные дашборды, AI-ассистент, автоматизация встреч и фолоуапов.

---

## 1. Что внутри

Репо содержит **три продукта**, которые работают вместе:

| Продукт | Путь | Назначение |
|---|---|---|
| **AM Hub** (основной) | `am-hub-final/` | FastAPI + PostgreSQL + Jinja2 — бэкенд, API, UI |
| **Редизайн UI** | `am-hub-final/static/design/` + `templates/design/` | Новый дизайн всех страниц (JSX → esbuild bundle) |
| **Chrome extension** | `extension/` | AM Hub · Sync — синхронизация Merchrules ↔ AM Hub |
| **Roadmap bulk-tasks** | `app.py` + корень | Отдельный мини-инструмент (старый, массовое создание задач) |

---

## 2. AM Hub — быстрый старт

### Локально (Python)

```bash
cd am-hub-final
python -m venv .venv
.venv\Scripts\activate     # Windows
# или: source .venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Открыть: `http://127.0.0.1:8000/login`

**Первый вход:** задайте админа через env:
```
INITIAL_ADMIN_EMAIL=admin@company.ru
INITIAL_ADMIN_PASSWORD=change-me-plz
```

### Через Docker

```bash
docker build -t am-hub .
docker run -p 8000:8000 --env-file am-hub-final/.env am-hub
```

### На Railway

Репо уже содержит `railway.json` + `nixpacks.toml`. После подключения репо:
1. Railway соберёт по `nixpacks.toml` (Python 3.12)
2. Добавьте env vars в UI (см. раздел Environment ниже)
3. Деплой стартует автоматически

---

## 3. Архитектура

```
┌─────────────────────────────────────────────────────────┐
│  Browser                                                 │
│  ├── /login, /dashboard, /clients, ...  (Jinja2 UI)      │
│  └── /design/*                           (JSX UI)        │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│  FastAPI (am-hub-final/main.py)                          │
│  ├── routers/auth.py, clients.py, tasks.py, meetings.py  │
│  ├── routers/analytics.py, ai.py, sync.py, ...           │
│  ├── routers/design.py          ← новый (редизайн)       │
│  └── sse.py                     ← real-time              │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│  PostgreSQL / SQLite                                     │
│  Models: Client, Task, Meeting, CheckUp, QBR, User, ...  │
└──────────────────────────────────────────────────────────┘
                   │
          ┌────────┴────────┬─────────────┐
          ▼                 ▼             ▼
    Merchrules API    Airtable       Telegram Bot
    (sync каждые      (клиенты,      (уведомления,
     15 мин)           QBR events)    задачи)
```

---

## 4. Редизайн UI (`/design/*`)

Новый интерфейс доступен параллельно со старым — **старые Jinja2-страницы продолжают работать**.

### Доступные страницы (19)

| URL | Компонент |
|---|---|
| `/design/command` | Командный центр |
| `/design/today` | Сегодня (таймлайн дня, брифы, задачи) |
| `/design/clients` | Все клиенты (paginated, 50/страница) |
| `/design/clients?page=2` | Следующая страница клиентов |
| `/design/client/{id}` | Карточка клиента (детальная) |
| `/design/top50` | Top-50 по GMV |
| `/design/tasks` | Задачи |
| `/design/meetings` | Встречи |
| `/design/portfolio` | Портфель |
| `/design/analytics` | Аналитика |
| `/design/ai` | AI-ассистент |
| `/design/kanban` | Канбан |
| `/design/kpi` | Мой KPI |
| `/design/qbr` | QBR Календарь |
| `/design/cabinet` | Мой кабинет |
| `/design/templates` | Шаблоны |
| `/design/auto` | Автозадачи |
| `/design/roadmap` | Роадмап |
| `/design/internal` | Внутренние задачи |
| `/design/help` | Помощь |
| `/design/extension` | Установить расширение |

### Фичи нового UI

- **Глобальный поиск** — ⌘K / Ctrl+K, ищет клиентов/задачи/встречи одновременно
- **FAB "+" (плавающая кнопка)** — создать задачу из любой страницы без перехода
- **Live sidebar-статы** — цифры обновляются из реальной БД (просроченное, скоро чекап, активные задачи)
- **External links** — KTalk и Merchrules открываются в новой вкладке
- **Per-user scoping** — менеджер видит только своих клиентов, admin — все
- **Настраиваемые пороги статуса** через env (см. ниже)

### Как собрать/пересобрать

Dizайн скомпилирован в `static/design/dist/bundle.js` (131 КБ). Если меняете JSX-исходники в `static/design/*.jsx`:

```bash
cd am-hub-final
npm install              # первый раз — ставит esbuild
npm run build:design     # собирает bundle.js за ~200мс
git add static/design/dist/bundle.js
git commit -m "rebuild design bundle"
git push                 # Railway подхватит
```

---

## 5. Environment variables

### Обязательные
| Variable | Зачем |
|---|---|
| `DATABASE_URL` | PostgreSQL URL (на Railway даётся автоматически) |
| `SECRET_KEY` | JWT-ключ для auth cookies |
| `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` | Создание админа при первом запуске (если БД пустая) |

### Интеграции (опционально, включаются по наличию)
| Variable | Интеграция |
|---|---|
| `MERCHRULES_LOGIN`, `MERCHRULES_PASSWORD`, `MERCHRULES_API_URL` | Основной источник данных |
| `AIRTABLE_TOKEN` (или `AIRTABLE_PAT`), `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID` | Синхронизация клиентов |
| `SHEETS_SPREADSHEET_ID` | Google Sheets экспорт |
| `KTALK_SPACE`, `KTALK_API_TOKEN` | Встречи |
| `TG_BOT_TOKEN`, `TG_NOTIFY_CHAT_ID` | Telegram-уведомления |
| `GROQ_API_KEY` / `QWEN_API_KEY` | AI (один из) |

### Редизайн (настраиваемые пороги)
| Variable | Default | Назначение |
|---|---|---|
| `HEALTH_RISK_MAX` | `0.55` | Ниже этого → клиент "risk" (красный) |
| `HEALTH_WARN_MAX` | `0.80` | Ниже этого → "warn" (жёлтый) |
| `HEALTH_STALE_MEETING_DAYS` | `30` | Сколько дней без встречи = просроченный чекап |

### Прод-настройки
| Variable | Назначение |
|---|---|
| `ENV=production` | Выключает `/docs`, `/redoc`, ставит secure cookies |
| `ALLOWED_ORIGINS` | Comma-separated origins для CORS (в проде обязательно явный список, `*` запрещён) |

Пример: `am-hub-final/.env.example`

---

## 6. Chrome Extension (AM Hub · Sync)

Синхронизирует данные из **Merchrules** в **AM Hub** каждые 30 минут (автоматически) + по кнопке.

### Установить

**Вариант A — из ZIP:**
1. Скачайте `extension/amhub-sync.zip`
2. Распакуйте
3. `chrome://extensions` → Developer mode → Load unpacked → выберите папку

**Вариант B — через dev-расширение:**
1. `chrome://extensions` → Developer mode
2. Load unpacked → выберите папку `extension/`

### Как настроить

Открыть popup → заполнить 4 поля:
- **Merchrules · логин** (email)
- **Merchrules · пароль**
- **AM Hub · URL** (напр. `https://your-hub.up.railway.app`)
- **AM Hub · токен** (`Bearer ...`)

Нажать "Сохранить и синхронизировать". При успехе — в логе расширения появится событие.

### Фичи popup'а
- 4 состояния статуса (connected / running / error / empty)
- Последние 4 события синхронизации
- Stats row: клиенты / задачи / события
- Ручной resync по кнопке в header (⟲)
- Bundled Inter Tight / JetBrains Mono (без CDN)

### Auto-update через git (Chrome native)

Расширение **самообновляется** у всех пользователей, когда вы пушите новую версию в master. Работает через стандартный Chrome-механизм `update_url` + подписанный `.crx` + `updates.xml` на raw.githubusercontent.

**Первоначальная настройка (один раз):**

```bash
cd extension
npm install          # ставит crx (dev-зависимость)
npm run pack         # генерит key.pem + amhub-sync.crx + updates.xml
```

**⚠️ `key.pem` в `.gitignore`** — храните его безопасно (в password manager или секретном storage команды). Без него вы не сможете выпускать обновления, а **потеря ключа** сменит Extension ID — все пользователи потеряют установленное.

**Выпуск новой версии:**

```bash
cd extension
npm run bump:patch            # 1.0.1 → 1.0.2 + rebuild .crx + updates.xml
# или
npm run bump:minor            # 1.0.x → 1.1.0

git add extension/manifest.json extension/amhub-sync.crx extension/updates.xml
git commit -m "ext: v1.0.2 — что изменилось"
git push
```

Через ~5 часов Chrome всех установивших пользователей опросит `updates.xml`, увидит новую версию и подтянет `.crx`.

**Форсированная проверка** (у пользователя):
- `chrome://extensions` → включить Developer mode → нажать "Обновить"

**Установка для конечного пользователя** (первый раз):
1. Скачать `extension/amhub-sync.crx` из репо
2. Перетащить файл в `chrome://extensions` (Developer mode включён)
3. Подтвердить установку
4. Дальше — расширение будет обновляться само, `.zip` не нужен

**Альтернатива для dev-режима** (если не хотите .crx):
1. `git clone` репо
2. `chrome://extensions` → Load unpacked → выбрать папку `extension/`
3. После `git pull` — вручную нажать "Обновить"

**Почему два артефакта (`.crx` и `.zip`)?**
- `.crx` — для end-users с auto-update
- `.zip` — для Web Store загрузки или ручной unpacked-установки без ключа

---

## 7. Как пользоваться AM Hub

### Основной флоу менеджера

1. **Утром** — открыть `/design/today`:
   - AI-бриф дня: ключевые встречи, риски, приоритеты
   - Таймлайн дня с цветовыми маркерами
   - Очередь задач (фильтры: все / мои / просроченные)

2. **Перед встречей** — открыть `/design/client/{id}`:
   - Карточка с GMV, trend, health, next touchpoint
   - Список предыдущих встреч и задач
   - Account Plan (quarterly targets, actions)

3. **После встречи** — автозадачи:
   - KTalk присылает транскрипт → AI парсит → предлагает задачи
   - В `/design/auto` — подтвердить / отредактировать / отправить в roadmap

4. **Раз в день** — проверить `/design/analytics`:
   - Churn-риск, NPS, revenue динамика
   - QBR-календарь

### Основной флоу команды

- **Командный центр** `/design/command` — общий пульс (все AM, агрегаты)
- **KPI** `/design/kpi` — личные метрики менеджера
- **Канбан** `/design/kanban` — задачи по стадиям
- **Автозадачи** `/design/auto` — задачи, созданные AI из встреч

---

## 8. Разработка

### Структура

```
am-hub-final/
├── main.py                  — FastAPI app + routing
├── models.py                — SQLAlchemy модели
├── database.py              — engine / sessions
├── auth.py                  — JWT + user management
├── schemas.py               — Pydantic DTOs
├── design_mappers.py        — ORM → design-dict конвертеры
├── routers/
│   ├── auth.py, clients.py, tasks.py, meetings.py, ...
│   └── design.py            — 20 роутов редизайна
├── templates/               — Jinja2 (старый UI)
│   └── design/app.html      — единый шаблон для нового UI
├── static/
│   ├── css/, js/            — старый UI
│   └── design/              — новый UI
│       ├── *.jsx            — исходники
│       └── dist/bundle.js   — прекомпилированный (коммитить)
├── integrations/            — Merchrules, Airtable, KTalk, ...
├── alembic/                 — миграции БД
├── build-design.mjs         — esbuild build script
└── package.json             — npm esbuild dependency
```

### Миграции БД

```bash
cd am-hub-final
alembic revision --autogenerate -m "описание"
alembic upgrade head
```

### Добавить новый роут в редизайн

1. Добавьте в `routers/design.py` → `PAGES` dict:
   ```python
   "myid": ("PageMyComponent", ["am hub", "раздел"], "Заголовок"),
   ```
2. Создайте `static/design/page_mypage.jsx` с функцией `PageMyComponent`
3. Добавьте в `window.PageMyComponent = PageMyComponent;` в конце файла
4. Обновите `build-design.mjs` ORDER массив
5. `npm run build:design`

### Тесты

```bash
cd am-hub-final
python -m pytest tests/       # если есть
```

Smoke-тест мапперов:
```bash
python -c "import design_mappers as dm; print(dm.HEALTH_RISK_MAX)"
```

---

## 9. Troubleshooting

| Проблема | Решение |
|---|---|
| `/design/*` не открывается | Проверьте, что `static/design/dist/bundle.js` существует (запустите `npm run build:design`) |
| Бандл устарел после правки JSX | `cd am-hub-final && npm run build:design && git add static/design/dist/bundle.js` |
| Первая загрузка медленная | Шрифты подгружаются с bunny.net. Для прода можно self-hosted (как в extension) |
| Empty nav stats | Нет клиентов в БД либо нет прав (проверьте role и UserClientAssignment) |
| Extension не синхронизирует | Проверьте `hub_url` — должен быть без trailing slash. Смотрите Console расширения |
| Admin не создан | Задайте `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` и рестартуйте — сработает на пустой БД |

---

## 10. Лицензия / контакты

Внутренний инструмент команды. Вопросы — в Telegram-канал команды AM Hub.

---

*Проект построен на FastAPI, React 18 (UMD), esbuild, SQLAlchemy, Jinja2. Деплой — Railway. Интеграции — Merchrules, Airtable, Google Sheets, KTalk, Telegram, Groq/Qwen AI.*
