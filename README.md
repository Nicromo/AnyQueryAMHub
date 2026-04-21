# AM Hub

**Enterprise Account Manager Dashboard** — единая операционная консоль для команды AM: реальные данные из Merchrules, персональные дашборды, AI-ассистент, автоматизация встреч и фолоуапов.

---

## 0. Основные фичи

### Портфель и клиенты
- **Командный центр** — единый инбокс событий (NPS детракторы / sync failures / ближайшие встречи / дедлайны)
- **Мой портфель** — таблица клиентов + структура портфеля сверху, bulk-actions (mark-checkup / onboarding / transfer), фильтр phantom-клиентов, тоггл «По ГК» (группы компаний с агрегатом MRR/GMV)
- **Карточка клиента** — AI-бриф, онбординг (с чекбоксом «не нужен»), voice notes, roadmap с DnD, upsell, merchrules dashboard (синонимы/whitelist/blacklist/rules), полный **дашборд метрик клиента** (MRR+sparkline / Health / NPS / Tasks / Meetings / Tickets / Upsell / Checkups)
- **Метрики клиента** — `/api/clients/{id}/metrics` отдаёт revenue_history / health_history / nps_history / tasks stats / meetings stats / tickets / upsell / checkups_recent
- **Группы компаний (ГК)** — объединение нескольких юр.лиц под одним брендом, админ-страница + toggle в портфеле (агрегат по MRR/GMV + worst-wins health)
- **Передача клиентов** — manager → manager с AI-сводкой + accept/decline; bulk-передача
- **Follow-up send** — AI-драфт → copy → отправка в TG → фиксация в Merchrules/Airtable/PartnerLog
- **Voice notes** — запись в браузере → Groq Whisper транскрипция → R2 storage
- **Onboarding** — 10-шаговый чеклист, автотаски, шаблоны писем, чекбокс «не нужен»

### Финансы и встречи
- **Оплаты** (заменили Renewal pipeline) — таблица неоплативших клиентов из **Airtable** таблицы финотдела `tblLEQYWypaYtAcp6`, fallback на БД. Фильтры и сортировка, бакеты срочности
- **Пульс портфеля NRR** — с fallback на `Client.mrr` если RevenueEntry пуст
- **Revenue trend** — ежедневный job пишет последние 12 MRR в `Client.revenue_trend` → бары «Динамика» в списке клиентов реально заполняются
- **Renewal (contract_end)** — отдельно в `/api/me/renewal-pipeline` (legacy, используется отдельно)
- **Upsell карточка** — события расширения MRR, статусы, дельты
- **Встречи** — список + месячный календарь (pure JSX без CDN), prep-слоты, follow-up tasks, подтягиваются прошлые встречи из Merchrules в Meeting таблицу

### Аналитика и AI
- **Аналитика портфеля** — фильтры по сегменту и точечно по клиенту, KPI / Heatmap активности (с fallback Meeting+Task+CheckupResult) / **Воронка чекапов** (реальная из Meeting+Task) / топ-риски
- **KPI** — NRR, NPS, portfolio MRR (с fallback), встречи за 60д, open/overdue tasks, следующая встреча
- **AI-ассистент** — 8 предзаданных промптов («Топ-10 в риске», «Кому пора QBR», «Где upsell», «Прогноз churn», «Бриф на неделю» и др.) + **data-grounding**: модель получает сводку портфеля и топ-40 клиентов с метриками в system prompt
- **Автозадачи** — 11 триггеров: health_drop, days_no_contact, checkup_due, payment_overdue, nps_low, task_blocked_days, **mrr_drop, contract_expiring, ticket_spike, upsell_window, stale_followup**

### Workflow и автоматизация
- **Merchrules dashboard** — синонимы, whitelist, blacklist, merch-rules (sync + create-rule из чекапа)
- **NPS workflow** — детрактор (≤6) → inbox + TG + автотаска менеджеру
- **Health score** — ежедневный recalc (3:00 МСК), тренды, PartnerLog
- **Задачи** — канбан/таблица toggle, внутренние задачи с полем «Исполнитель» (assignee), время выполнения (HH:MM) для точных напоминаний
- **Scope switcher** — «мои / моя группа / все» на всех страницах (для grouphead/admin)
- **Chrome-расширение** — action log внизу popup, креды переживают reinstall (storage.sync + /api/extension/config restore), ZIP всегда свежий (dynamic endpoint), авторефреш статуса токенов
- **Roadmap** — DnD между Q1-Q4/backlog + sort внутри колонки, inline-edit, per-client roadmap через Task.meta
- **CSM-change detection** — если в Airtable сменился CSM у клиента → старый менеджер получает notification, pending `ClientTransferRequest` создаётся автоматически

### Остальное
- **Чекапы** — результаты из Chrome расширения → кнопка «Создать правило в Merchrules» → draft rule + PartnerLog + задача
- **Health probe** — `/health` и `/health/deep` для мониторинга
- **Command Bar на мобилке** — нативный bottom-sheet на узких экранах

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
| `MERCHRULES_LOGIN`, `MERCHRULES_PASSWORD`, `MERCHRULES_API_URL` | Основной источник клиентов/задач/встреч |
| `AIRTABLE_TOKEN` (или `AIRTABLE_PAT`), `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID` | Синхронизация клиентов (default base `appEAS1rPKpevoIel`) |
| `AIRTABLE_PAYMENTS_TABLE_ID`, `AIRTABLE_PAYMENTS_VIEW_ID` | Таблица просроченных оплат для `/design/renewal` (default `tblLEQYWypaYtAcp6` / `viw977k6GUNrkeRRy`) |
| `AIRTABLE_QBR_TABLE_ID` | QBR-события (default `tblqQbChhRYoZoxWu`) |
| `HUB_URL` | Абсолютный URL хаба для Chrome-расширения. Используется в `/api/extension/version` → `download_url` и `install_url`. Если не задан — подставится `request.base_url` |
| `SHEETS_SPREADSHEET_ID` | Google Sheets экспорт |
| `KTALK_SPACE`, `KTALK_API_TOKEN` | Встречи |
| `TG_BOT_TOKEN`, `TG_NOTIFY_CHAT_ID` | Telegram-уведомления |
| `GROQ_API_KEY` / `QWEN_API_KEY` | AI (один из) |
| `GROQ_WHISPER_MODEL` | Модель голоса (default `whisper-large-v3`) |
| `CF_R2_ACCOUNT_ID`, `CF_R2_ACCESS_KEY`, `CF_R2_SECRET_KEY`, `CF_R2_BUCKET`, `CF_R2_PUBLIC_URL` | R2 storage для voice-notes + вложений (fallback — локальный `/tmp`) |
| `AMHUB_CRYPTO_KEY` | Fernet-ключ для шифрования паролей Merchrules/Ktalk в user.settings (44 символа base64, без него пароли сохраняются в plaintext) |
| `REDIS_URL` / `UPSTASH_REDIS_URL` | Redis для `/health/deep` probe (опционально) |
| `EXT_CHANGELOG` | Текст changelog для уведомления об апдейте расширения |

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

## 7.5. Тесты

Базовые smoke-тесты в `am-hub-final/tests/`:
```bash
cd am-hub-final
pip install pytest
DATABASE_URL=sqlite:///./_test.db SECRET_KEY=x pytest -q tests/
```

Проверяют: импорты всех ключевых роутеров, registered paths, бизнес-функции (renewal bucket, Fernet roundtrip, audio MIME whitelist).
БД-зависимые тесты требуют Postgres — будут добавлены отдельно.

CI-пайплайн (GitHub Actions):
- **Lint Python** — ruff + `py_compile` (hard gate)
- **Check JS** — `node --check` по всем `.js`
- **Pytest smoke** — новый smoke-job с sqlite DATABASE_URL

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
| Extension не синхронизирует | В popup внизу «Журнал действий» покажет ошибку. Проверьте HUB_URL/HUB_TOKEN. После uninstall креды подтянутся автоматически из storage.sync или `/api/extension/config` |
| Extension: «Токен не найден» после логина в time.tbank.ru | Обновите расширение до v3.3.2+ — раньше была ошибка с ключом `tbank_time_token` vs `time_token` |
| Admin не создан | Задайте `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` и рестартуйте — сработает на пустой БД |
| MR sync возвращает «1 клиент» | Зайдите в `/design/profile` → «Мои кабинеты Merchrules», впишите свои `site_id` (через запятую/новую строку) и «Сохранить». Sync будет тянуть их поштучно, а не глобальный `/accounts` |
| NRR показывает 0 / «Failed to fetch» | После PR #87 endpoint возвращает soft-empty вместо 500. После PR #99 добавлен fallback на `Client.mrr`. Запустите job: `POST /api/revenue-trend/update` (только admin) |
| Heatmap активности пустой | PR #101: добавлен fallback на Meeting+Task+CheckupResult если AuditLog пуст |
| Воронка чекапов 0/0/0 | PR #101: новый endpoint `/api/analytics/checkup-funnel` считает из реальных данных |
| Страница `/design/renewal` пустая | PR #97: теперь тянет из Airtable-таблицы финотдела по manager_email. Если таблица не настроена → fallback на БД `Client.payment_*`. Проверьте `AIRTABLE_TOKEN` и что в таблице `♥️Оплачено CSM` указан ваш email |
| CSM поменялся в Airtable, но в AM Hub — старый | PR #98: смена CSM не затирает `manager_email` автоматически. В инбоксе старого менеджера появляется уведомление и pending `ClientTransferRequest` — нужно явно Accept |

---

## 9.5. Архитектура интеграций (cheat sheet)

```
Airtable (appEAS1rPKpevoIel)
├── tblIKAi1gcFayRJTn        → Clients (sync раз в 30 мин)
├── tblLEQYWypaYtAcp6 (view) → Payments pending → /design/renewal
└── tblqQbChhRYoZoxWu        → QBR events

Merchrules
├── /backend-v2/sites/{id}  → по user.settings.merchrules.my_site_ids (PR #98)
└── /backend-v2/meetings    → upsert в Meeting (PR #96)

Chrome Extension (v3.3.3+)
├── storage.sync            → переживает reinstall
├── /api/extension/config   → восстановление кредов с хаба
├── /api/extension/download → dynamic ZIP, всегда свежий
└── /api/auth/tokens/push   → time_token + ktalk_token, auto-sync тикетов

AI (Groq + Qwen)
├── /api/ai/chat            → data-grounding (топ-40 клиентов с метриками)
└── PageAI presets          → 8 промпт-шаблонов

Scheduler (APScheduler, МСК)
├── mr_sync        :30min   → Merchrules
├── health_recalc  03:00    → пересчёт health_score
├── revenue_trend  03:30    → Client.revenue_trend для спарклайнов
├── auto_task_rules: 11 триггеров (+5 новых из PR #96)
└── qbr_auto_collect, weekly_digest, morning_plan, meeting_reminder_30min, …
```

---

## 10. Лицензия / контакты

Внутренний инструмент команды. Вопросы — в Telegram-канал команды AM Hub.

---

*Проект построен на FastAPI, React 18 (UMD), esbuild, SQLAlchemy, Jinja2. Деплой — Railway. Интеграции — Merchrules, Airtable, Google Sheets, KTalk, Telegram, Groq/Qwen AI.*
