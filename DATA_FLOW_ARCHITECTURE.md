# Архитектура интеграций AM Hub - Диаграмма потоков данных

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL DATA SOURCES                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│   Airtable       │   │   Merchrules     │   │   Ktalk/Tbank    │
│   ┌────────────┐ │   │   ┌────────────┐ │   │   ┌────────────┐ │
│   │ Clients    │ │   │   │ Roadmap    │ │   │   │ Meetings   │ │
│   │ Managers   │ │   │   │ Meetings   │ │   │   │ Recording  │ │
│   │ QBR Dates  │ │   │   │ Analytics  │ │   │   │ Transcript │ │
│   │ Segments   │ │   │   │ Checkups   │ │   │   │ Artifacts  │ │
│   │ Account #  │ │   │   │ Feed       │ │   │   └────────────┘ │
│   └────────────┘ │   │   └────────────┘ │   │                  │
└────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
         │                      │                       │
         │                      │                       │
         ▼                      ▼                       ▼
┌────────────────┐   ┌────────────────┐   ┌───────────────────┐
│  airtable.py   │   │merchrules_ext. │   │    ktalk.py       │
│                │   │     py         │   │                   │
│ • get_clients()│   │                │   │• get_meetings()   │
│ • update_date()│   │•fetch_analytics│   │• get_transcript() │
│                │   │•fetch_tasks()  │   │•get_recording()   │
└────────┬───────┘   │•fetch_meetings │   └─────────┬─────────┘
         │           │•fetch_checkups │              │
         └───────────┴────┬───────────┘              │
                          │                          │
         ┌────────────────┴──────────────────────────┘
         │
         ▼ TRANSFORM & ENRICH
┌──────────────────────────────────────────────────┐
│           AM Hub FastAPI Application             │
│  ┌──────────────────────────────────────────┐   │
│  │        DATA MODELS (SQLAlchemy)          │   │
│  │  ┌──────────┐  ┌──────────┐  ┌────────┐ │   │
│  │  │ Clients  │  │ Tasks    │  │Meetings│ │   │
│  │  └──────────┘  └──────────┘  └────────┘ │   │
│  │  ┌──────────┐  ┌──────────────────────┐ │   │
│  │  │CheckUps  │  │  SyncLog (debug)     │ │   │
│  │  └──────────┘  └──────────────────────┘ │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │     APScheduler (Background Jobs)        │   │
│  │  • Hourly: sync_all_accounts_data()      │   │
│  │  • Daily: check_overdue_checkups()       │   │
│  │  • Every 30min: meeting_reminders()      │   │
│  │  • Weekly: digest & analytics()          │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │         FastAPI Endpoints                │   │
│  │  GET /api/clients                        │   │
│  │  GET /api/clients/<id>/health-score      │   │
│  │  GET /api/clients/<id>/tasks             │   │
│  │  GET /api/clients/<id>/checkups          │   │
│  │  POST /api/meetings                      │   │
│  │  GET /api/feed                           │   │
│  └──────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
         │                                    │
         │                                    │
         ▼                                    ▼
┌──────────────────┐   ┌──────────────────────────┐
│  PostgreSQL DB   │   │  Frontend Templates      │
│  ┌────────────┐  │   │  ┌────────────────────┐  │
│  │ clients    │  │   │  │ workspace.html     │  │
│  │ tasks      │  │   │  │ hub.html           │  │
│  │ meetings   │  │   │  │ tasks.html         │  │
│  │ checkups   │  │   │  │ qbr.html           │  │
│  │ sync_logs  │  │   │  │ analytics.html     │  │
│  └────────────┘  │   │  │ profile.html       │  │
└──────────────────┘   │  └────────────────────┘  │
                       └──────────────────────────┘
         │ (two-way)           │ (display)
         │                     │
         └─────────┬───────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │   Telegram Bot       │
        │  (notifications)     │
        │  /start, /help       │
        │  /top50, /checkups   │
        └──────────────────────┘

         ┌─────────────────────────────────────────┐
         │   Optional: Dashboard (two-way sync)    │
         │   /api/sync/<resource>                  │
         │   PULL ↔ System ↔ PUSH                 │
         └─────────────────────────────────────────┘
```

## Поток синхронизации данных

```
1. AIRTABLE SYNC (hourly)
   ┌──────────────────┐
   │ get_clients()    │ → Получить список клиентов + менеджеры
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │ Сохранить в БД   │ → Client.airtable_record_id = record_id
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐
   │ Обновить metadata│ → integration_metadata['airtable'] = {...}
   └──────────────────┘


2. MERCHRULES SYNC (hourly)
   ┌──────────────────────────────┐
   │ Для каждого клиента:         │
   │ • account_id = ?             │
   │ • fetch_sales_analytics()    │ → Health Score, Revenue Trend
   │ • fetch_roadmap_tasks()      │ → Open Tasks
   │ • fetch_meetings()           │ → Recent meetings
   │ • fetch_checkups()           │ → Overdue?
   │ • fetch_feed()               │ → Recent activity
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Обновить Client в БД:        │
   │ • health_score               │
   │ • revenue_trend              │
   │ • open_tasks (count)         │
   │ • last_meeting_date          │
   │ • needs_checkup              │
   │ • last_sync_at               │
   └──────────────────────────────┘


3. MEETINGS SYNC (hourly)
   ┌──────────────────────────────┐
   │ Получить встречи:            │
   │ • Из Ktalk (если авто-синк)  │
   │ • Из Merchrules              │
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Для каждой встречи:          │
   │ • Скачать запись             │
   │ • Получить транскрипцию      │
   │ • Генерировать AI summary    │
   │ • Выделить задачи            │
   │ • Создать Task записи        │
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Сохранить Meeting в БД:      │
   │ • transcript (full text)     │
   │ • summary (AI-generated)     │
   │ • created_tasks (relations)  │
   │ • source = ktalk|merchrules  │
   └──────────────────────────────┘


4. SUPPORT TICKETS SYNC (hourly)
   ┌──────────────────────────────┐
   │ Для каждого клиента:         │
   │ count_open_tickets()         │ → Получить открытые tickets
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Обновить Client.open_tickets │
   │ Обновить Client.last_ticket_ │
   │ date (если был новый)        │
   └──────────────────────────────┘


5. CHECKUP ALERTS (daily 08:00)
   ┌──────────────────────────────┐
   │ SELECT * FROM checkups       │
   │ WHERE status = 'overdue'     │
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Для каждого overdue:         │
   │ • Создать Task (priority=hi) │
   │ • Отправить Telegram alert   │
   │ • Обновить Client.needs_chec │
   │   kup = True                 │
   └──────────────────────────────┘


6. DASHBOARD SYNC (on-demand)
   ┌──────────────────────────────┐
   │ pull_updates("tasks")        │ ← Получить обновления из дашборда
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Применить обновления локально│
   │ (conflict resolution)        │
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ Создать Task/Meeting/etc     │
   │ Обновить статусы             │
   └────────────┬─────────────────┘
                │
                ▼
   ┌──────────────────────────────┐
   │ push_updates("tasks")        │ → Отправить свои изменения
   └──────────────────────────────┘
```

## Пример: Поток встречи QBR

```
1. Менеджер заводит встречу в Ktalk
   Ktalk: Meeting created → artifacts/recording stored

2. AM Hub hourly sync
   integrations/ktalk.py: get_meetings()
   ↓
   Get recording & transcript

3. AI Processing
   Groq API: Generate summary, extract key points, identify tasks
   ↓
   Summary: "Обсудили Q2 roadmap, нужны доп ресурсы для AI feature..."
   Tasks: 
     - Allocate dev team for AI feature
     - Budget approval needed
     - Setup testing env

4. Database Save
   ├─ Meeting record
   │  ├─ transcript (full)
   │  ├─ summary (AI)
   │  ├─ recording_url
   │  └─ created_tasks [task1, task2, task3]
   │
   ├─ Task records (3)
   │  ├─ source = "meeting"
   │  ├─ created_from_meeting_id = meeting.id
   │  └─ status = "plan"
   │
   └─ Update Client
      ├─ last_meeting_date = now
      ├─ health_score (if improved)
      └─ activity_level = "high"

5. Notifications
   ├─ Telegram: Manager notified of new meeting + summary
   ├─ Airtable: Update meeting_date + add comment with summary
   └─ Dashboard: Push meeting & tasks (if integrated)

6. Frontend Display
   workspace.html: 
     - Show meeting card
     - Display summary
     - List created tasks
     - Let manager add more tasks
```

## Синхронизация состояний

```
┌──────────────────────────────────────────────────────────┐
│              Task Status Sync Cycle                       │
└──────────────────────────────────────────────────────────┘

Local DB                  Merchrules                 Airtable
├─ plan                   ├─ plan                    ├─ draft
├─ in_progress           ──┼─ in_progress         ──┼─ in_work
├─ blocked               ──┼─ blocked              ──┼─ blocked
└─ done                  ──┴─ done                 ──┴─ complete

Sync Logic:
1. Pull from Merchrules → Update local status
2. Pull from Dashboard → Merge updates
3. Conflict: Merchrules is source of truth
4. Push back to Airtable (for reporting)
```
