# 🏗️ Архитектура интеграций AM Hub

## Структура источников данных

```
AM Hub
├── Airtable (CRM клиентов, QBR календарь)
│   ├── Клиенты + менеджеры
│   ├── Номера аккаунтов
│   ├── Сегменты
│   └── QBR календарь
│
├── Merchrules (основная система)
│   ├── /roadmap → Задачи по аккаунтам
│   ├── /meetings → Встречи
│   ├── /analytics/full → Метрики здоровья
│   ├── /checkups → Чекапы
│   ├── /search-settings → Фильтры
│   ├── /feeds → Активность
│   └── /feed-processing → Статус обработки
│
├── Ktalk/Tbank (видеовстречи)
│   ├── /tbank.ktalk.ru → Список встреч
│   └── /content/artifacts → Записи + транскрипции
│
├── Tbank Time (саппорт)
│   └── /channels/any-team-support → Обращения
│
└── Дашборд
    └── Двусторонняя синхронизация
```

## Модули интеграций

### 1. **integrations/airtable.py**
- `get_clients()` - Получить список всех клиентов + менеджеры
- `get_account_id(client_name)` - По названию клиента получить ID аккаунта
- `update_meeting_date(client_id, date)` - Обновить дату последней встречи
- `sync_qbr_calendar()` - Синхронизировать QBR календарь
- Кэширование на 15 минут

### 2. **integrations/merchrules.py** (существует)
**Нужно расширить:**
- `fetch_account_analytics(account_id)` - /analytics/full для Account Health Score
- `fetch_checkups(account_id)` - /checkups для overdue checkups
- `fetch_feed(account_id)` - /feeds последнюю активность
- `get_search_settings(account_id)` - Применить фильтры поиска

### 3. **integrations/ktalk.py** (новое)
- `get_meetings_list()` - Список встреч из Ktalk
- `get_meeting_recording(meeting_id)` - Получить запись встречи
- `get_meeting_transcript(meeting_id)` - Получить транскрипцию
- Синхронизировать с `Meeting` моделью в БД

### 4. **integrations/tbank_time.py** (новое)
- `get_support_tickets(client_id)` - Обращения в саппорт по клиенту
- `get_ticket_status(ticket_id)` - Статус обращения
- Связать с `Client.open_tickets`

### 5. **integrations/dashboard.py** (новое)
- Двусторонняя синхронизация
- Push: отправить обновления в дашборд
- Pull: получить изменения из дашборда

## Модель данных (расширена)

```
Client
├── id (Airtable record ID)
├── name
├── account_id (номер аккаунта в Merchrules)
├── manager_id / manager_email (из Airtable)
├── segment (из Airtable)
├── site_ids (array)
├── health_score (из Merchrules analytics)
├── last_meeting_date (из Merchrules + Ktalk)
├── needs_checkup (из Merchrules checkups)
├── open_tickets (из Tbank Time)
├── revenue_trend (из Merchrules analytics)
└── integration_metadata
    ├── airtable_record_id
    ├── merchrules_account_id
    ├── ktalk_entity_id
    └── last_sync_at

Task
├── id
├── client_id
├── merchrules_id (для связи)
├── title
├── description
├── status (plan/in_progress/blocked/done)
├── priority
├── due_date
└── source (roadmap/checkup/feed)

Meeting
├── id
├── client_id
├── date
├── type (checkup/qbr/kickoff/etc)
├── source (ktalk/merchrules/internal)
├── recording_url
├── transcript (текст встречи)
├── summary (из AI)
├── mood (сентимент)
└── created_tasks (связь с Task для созданных задач)

CheckUp
├── id
├── client_id
├── status (overdue/scheduled/completed)
├── scheduled_date
├── priority
```

## Планировщик (APScheduler)

```
09:00 Mon-Fri    → Morning Plan
  ├── fetch_checkups() → Overdue checkups
  ├── fetch_today_tasks() → Задачи на сегодня
  └── notify_managers()

17:00 Fri        → Weekly Digest
  ├── analytics по всем клиентам
  ├── summary встреч за неделю

Hourly           → Sync Data
  ├── sync_merchrules_tasks()
  ├── sync_airtable_clients()
  ├── sync_ktalk_meetings()
  └── sync_tbank_tickets()

Every 30 min     → Meeting Reminders
  ├── Check upcoming meetings (24h + 1h)
  └── Notify managers

Daily 08:00      → Auto Checkup Tasks
  ├── fetch checkups
  └── Create tasks for overdue
```

## Флоуы данных

### Как информация попадает в систему:
1. **Клиент загружается** → Airtable sync → DB
2. **Встреча в Ktalk** → APScheduler hourly → Download transcript → AI summary → Create tasks if needed
3. **Задача в Merchrules** → APScheduler sync → Update in DB
4. **Checkup overdue** → APScheduler daily → Create notification + auto task
5. **Support ticket в Time** → APScheduler sync → Attach to client

### Как информация уходит из системы:
1. **Изменение status задачи** → Sync to Merchrules
2. **Добавление комментария** → Flush to Airtable + Merchrules
3. **Встреча завершена** → Update meeting_date in Airtable

## Приоритет реализации

- **MVP (Phase 1)**: Airtable клиенты + Merchrules встречи/задачи + APScheduler синхронизация
- **Phase 2**: Ktalk интеграция + транскрипции + AI summary
- **Phase 3**: Tbank Time интеграция + дашборд синхронизация
