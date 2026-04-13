# AM Hub — Полный текущий функционал
> Состояние на: Апрель 2026
> Версия: 2.0.0 (Enterprise)

---

## 📁 СТРУКТУРА ПРОЕКТА

```
am-hub-final/
├── main.py                  # FastAPI приложение (~1850 строк)
├── models.py                # SQLAlchemy модели (10 таблиц)
├── database.py              # DB connection
├── auth.py                  # JWT авторизация
├── creds.py                 # Персональные настройки/креды пользователей
├── scheduler.py             # APScheduler (фоновые задачи)
├── config.py                # Pydantic конфигурация
├── middlewares.py           # Logging, RateLimit, Error, Security
├── error_handlers.py        # Кастомные исключения
├── monitoring.py            # Prometheus метрики
├── validators.py            # Pydantic валидаторы
├── file_service.py          # Импорт/экспорт CSV/Excel/PDF
├── email_service.py         # SMTP/SendGrid/Postmark
├── websocket_manager.py     # WebSocket real-time
├── tg_bot.py                # Telegram Bot (webhook)
├── tg.py                    # TG канал followup
├── sheets.py                # Google Sheets Top-50
├── merchrules_sync.py       # Merchrules sync (tasks, meetings)
├── merchrules.py            # Merchrules push
├── airtable_sync.py         # Airtable sync
├── ai_assistant.py          # AI (Groq/Qwen) prep, followup, risks
├── ai_followup.py           # AI transcript processing
├── qwen_api.py              # Qwen (DashScope) AI
├── ktalk.py                 # Контур.Толк интеграция
├── integrations/
│   ├── ktalk.py             # Контур.Толк API (events, transcripts)
│   ├── tbank_time.py        # Tbank Time (tickets)
│   ├── airtable.py          # Airtable API
│   ├── merchrules_extended.py # Merchrules extended
│   └── dashboard.py         # Dashboard API
└── templates/               # Jinja2 HTML шаблоны (14 страниц)
```

---

## 🗄️ БАЗА ДАННЫХ (10 таблиц)

| Таблица | Описание | Ключевые поля |
|---------|----------|---------------|
| **users** | Пользователи системы | email, role, settings(JSONB), telegram_id |
| **clients** | Клиенты/аккаунты | name, segment, health_score, site_ids, last_qbr_date, next_qbr_date, account_plan |
| **tasks** | Задачи | title, status, priority, team, confirmed_at, pushed_to_roadmap |
| **meetings** | Встречи | date, type, followup_status, followup_text, is_qbr |
| **checkups** | Чекапы | type, status, scheduled_date, priority |
| **qbrs** | Quarterly Business Review | quarter, metrics, summary, achievements, issues, future_work, presentation_url, executive_summary |
| **account_plans** | Планы работы по клиенту | quarterly_goals, action_items, notes, strategy |
| **audit_logs** | Логи действий | user_id, action, resource_type, old_values, new_values |
| **notifications** | Уведомления | user_id, title, message, type, is_read |
| **sync_logs** | Логи синхронизации | integration, status, records_processed |

---

## 🌐 HTML СТРАНИЦЫ (14 шаблонов)

| Страница | URL | Описание |
|----------|-----|----------|
| **Login** | `/login` | Авторизация (email/password + JWT cookie) |
| **Onboarding** | `/onboarding` | Тур по системе (5 шагов, первый вход) |
| **Dashboard** | `/dashboard` | Главный экран: KPI, графики, карточки действий, таблица клиентов |
| **My Day** | `/today` | Задачи и встречи на сегодня + просроченные |
| **Clients** | `/clients` | Список клиентов с фильтрами по сегментам |
| **Client Detail** | `/client/{id}` | Задачи + встречи клиента + ссылки на Prep/Followup/Plan/QBR |
| **Prep** | `/prep/{id}` | AI-подготовка к встрече |
| **Followup** | `/followup/{id}` | AI-фолоуап после встречи |
| **Plan** | `/client/{id}/plan` | План работы: цели, действия, стратегия, заметки |
| **QBR** | `/client/{id}/qbr` | Quarterly Business Review: достижения, проблемы, инсайты, презентация, выжимка |
| **Tasks** | `/tasks` | Список всех задач с фильтрами по статусу |
| **Sync** | `/sync` | Синхронизация с Merchrules |
| **Integrations** | `/integrations` | Статус всех интеграций |
| **Settings** | `/settings` | Персональные настройки: креды, правила, тема, уведомления |

---

## 🔌 API ENDPOINTS

### Auth
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/login` | Страница логина |
| POST | `/login` | Вход (email+password → JWT cookie) |
| GET | `/logout` | Выход |

### Pages
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/dashboard` | Дашборд (с проверкой онбординга) |
| GET | `/onboarding` | Онбординг-тур |
| GET | `/today` | Мой день |
| GET | `/clients` | Список клиентов |
| GET | `/client/{id}` | Детали клиента |
| GET | `/prep/{id}` | Подготовка к встрече |
| GET | `/followup/{id}` | Фолоуап |
| GET | `/client/{id}/plan` | План работы |
| GET | `/client/{id}/qbr` | QBR |
| GET | `/tasks` | Список задач |
| GET | `/sync` | Страница синхронизации |
| GET | `/integrations` | Страница интеграций |
| GET | `/settings` | Настройки |
| GET | `/` | Редирект на dashboard/login |
| GET | `/health` | Health check |

### Settings
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/settings/creds` | Сохранить персональные креды |
| POST | `/api/settings/rules` | Сохранить правила работы |
| POST | `/api/settings/prefs` | Сохранить оформление/уведомления |

### Onboarding
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/onboarding/complete` | Отметить онбординг пройденным |
| GET | `/api/onboarding/status` | Проверить статус онбординга |

### Admin
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/admin/reset-data` | Удалить все данные (только админ) |

### Workflow: Followup
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/meetings/{id}/followup/generate` | AI-генерация фолоуапа |
| POST | `/api/meetings/{id}/followup/send` | Подтвердить отправку → задача done |
| POST | `/api/meetings/{id}/followup/skip` | Пропустить → задача plan |

### Workflow: Tasks
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/tasks` | Создать задачу |
| PUT | `/api/tasks/{id}` | Обновить задачу |
| POST | `/api/tasks/{id}/confirm` | Подтвердить выполнение |
| POST | `/api/tasks/{id}/push-roadmap` | Отправить в Merchrules Roadmap |

### Workflow: QBR
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/clients/{id}/qbr` | Получить QBR данные |
| POST | `/api/clients/{id}/qbr` | Создать/обновить QBR |

### Workflow: Plan
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/clients/{id}/plan` | Получить план работы |
| POST | `/api/clients/{id}/plan` | Сохранить план работы |

### Workflow: Dashboard
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/dashboard/actions` | Карточки действий (followup/prep/checkup/qbr) |

### Integrations: Merchrules
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/sync/merchrules` | Синхронизация клиентов+задач из Merchrules |
| GET | `/api/integrations/test/merchrules` | Тест подключения Merchrules |

### Integrations: Ktalk (Контур.Толк)
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/ktalk/notify` | Отправить уведомление в Ktalk |
| POST | `/api/ktalk/followup` | Отправить фолоуап в Ktalk |
| GET | `/api/integrations/test/ktalk` | Тест подключения Ktalk |

### Integrations: Tbank Time
| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/api/tbank/tickets/{name}` | Тикеты для клиента |
| GET | `/api/tbank/tickets` | Все тикеты |
| GET | `/api/integrations/test/tbank` | Тест подключения |

### Integrations: AI
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/ai/process-transcript` | Обработка транскрипции AI |
| POST | `/api/ai/generate-followup` | AI-генерация фолоуапа |

### Telegram
| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/webhook/telegram` | Webhook Telegram Bot |

---

## 🔗 ИНТЕГРАЦИИ

### ✅ Реализованные и рабочие

| Интеграция | Статус | Переменные | Описание |
|-----------|--------|------------|----------|
| **Merchrules** | ✅ | `MERCHRULES_LOGIN`, `MERCHRULES_PASSWORD` | Задачи, встречи, аналитика дорожной карты |
| **Контур.Толк (Ktalk)** | ✅ | `KTALK_SPACE`, `KTALK_API_TOKEN` | Встречи, транскрипции, записи, комнаты, пользователи |
| **Tbank Time** | ⚠️ | `TIME_API_TOKEN` | Тикеты поддержки (заглушка API) |
| **Airtable** | ⚠️ | `AIRTABLE_PAT`, `AIRTABLE_BASE_ID` | База клиентов, чекапы |
| **Google Sheets** | ⚠️ | `SHEETS_SPREADSHEET_ID` | Top-50 рейтинг клиентов |
| **AI (Groq)** | ✅ | `GROQ_API_KEY` | AI-помощник (prep, followup, risks) |
| **AI (Qwen)** | ✅ | `QWEN_API_KEY` | Fallback AI (DashScope, 1M токенов/мес бесплатно) |
| **Telegram Bot** | ✅ | `TG_BOT_TOKEN`, `ALLOWED_TG_IDS` | Бот с командами /status, /checkups, /tasks, /prep, /top50 |
| **Email** | ✅ | `SENDGRID_API_KEY` или SMTP | Утренние планы, алерты |
| **Контур.Толк (старый)** | ❌ | `KTALK_WEBHOOK_URL` | Устарел, заменён на новый API |

### Интеграционные модули (файлы)

| Файл | Функции | Статус |
|------|---------|--------|
| `merchrules_sync.py` | `get_auth_token`, `fetch_site_tasks`, `fetch_site_meetings`, `sync_clients_from_merchrules`, `get_client_metrics` | ✅ |
| `integrations/ktalk.py` | `get_events`, `get_transcript`, `get_recording_url`, `get_rooms`, `get_users`, `get_audit_log`, `sync_meetings_for_client` | ✅ |
| `integrations/tbank_time.py` | `get_support_tickets`, `sync_tickets_for_client` | ⚠️ Заглушка API |
| `integrations/airtable.py` | `get_clients`, `update_meeting_date` | ⚠️ |
| `sheets.py` | `fetch_sheet_csv`, `get_top50_data` | ✅ |
| `ai_assistant.py` | `generate_prep_brief`, `generate_smart_followup`, `detect_account_risks` | ✅ (Groq→Qwen fallback) |
| `ai_followup.py` | `process_transcript` | ✅ |
| `tg_bot.py` | `handle_update`, `send_message`, команды бота | ✅ |
| `email_service.py` | `send_morning_plan`, `send_overdue_checkup_alert`, `send_task_created` | ✅ |

---

## 👤 РОЛИ ПОЛЬЗОВАТЕЛЕЙ

| Роль | Доступ |
|------|--------|
| **admin** | Всё: управление клиентами, удаление, sync, reset-data |
| **manager** | Свои клиенты (по manager_email), задачи, встречи, QBR, plan |
| **viewer** | Только просмотр (ограничено) |

---

## ⚙️ ФОНОВЫЕ ЗАДАЧИ (Scheduler)

| Задача | Расписание | Описание |
|--------|------------|----------|
| `job_sync_airtable_clients` | Каждый час | Sync клиентов из Airtable |
| `job_sync_merchrules_analytics` | Каждый час | Sync аналитики из Merchrules |
| `job_sync_roadmap_tasks` | Каждый час | Sync задач из Roadmap |
| `job_sync_meetings` | Каждый час | Sync встреч |
| `job_check_overdue_checkups` | Ежедневно 08:00 | Проверка просроченных чекапов |
| `job_morning_plan` | Пн-Пт 09:00 | Утренний план |
| `job_weekly_digest` | Пятница 17:00 | Еженедельный дайджест |

---

## 🎨 ТЕМА И ОФОРМЛЕНИЕ

- Тёмная тема (по умолчанию)
- Светлая тема
- Переключатель в sidebar + в настройках
- Сохранение в localStorage

---

## 📊 ИНТЕРВАЛЫ ЧЕКАПОВ (предзаданы)

| Сегмент | Сумма | Интервал |
|---------|-------|----------|
| SS | до 15 000 ₽ | 1 раз в 6 мес. |
| SMB | 15 000 – 29 999 ₽ | 1 раз в 3 мес. |
| SME | 30 000 – 99 999 ₽ | 1 раз в 2 мес. |
| ENT | от 100 000 ₽ | 1 раз в месяц |

---

## 🔐 ПЕРСОНАЛЬНЫЕ НАСТРОЙКИ (JSONB в User.settings)

```json
{
  "merchrules": {"login": "...", "password": "..."},
  "telegram": {"bot_token": "...", "chat_id": "..."},
  "ktalk": {"space": "...", "api_token": "..."},
  "tbank_time": {"api_token": "..."},
  "rules": {
    "warning_days": 14,
    "auto_create_tasks": true,
    "morning_plan_time": "09:00",
    "weekly_digest_day": "friday"
  },
  "preferences": {
    "theme": "dark",
    "dashboard_view": "cards",
    "notifications_email": true,
    "notifications_tg": true,
    "notifications_ktalk": false,
    "notif_overdue": true,
    "notif_new_tasks": true,
    "notif_blocked": true,
    "notif_morning": true
  },
  "onboarding_complete": true
}
```

---

## 🚀 DEPLOY

| Платформа | Файл | Описание |
|-----------|------|----------|
| Railway | `nixpacks.toml` | Автоматический деплой из GitHub |
| Docker | `Dockerfile` | Контейнеризация |

---

## ❌ НЕИСПОЛЬЗУЕМЫЕ ФАЙЛЫ (можно удалить)

- `main_backup.py` — бэкап старой версии
- `seed_db.py` — тестовые данные (больше не нужны)
- `integrations/dashboard.py` — заглушка
- `index_new.html`, `workspace.html`, `hub.html`, `analytics.html`, `roadmap.html`, `qbr_calendar.html`, `checklist.html`, `profile.html`, `my_clients.html`, `internal_tasks.html`, `top50.html`, `base.html` — шаблоны без роутов

---

## 💡 ПРЕДЛОЖЕНИЯ ПО НОВОМУ ФУНКЦИОНАЛУ

### Приоритет 1 (критично для работы)
1. **Календарь встреч** — визуальный календарь (FullCalendar) со всеми встречами клиента
2. **Страница аналитики** — графики трендов health score, задач, встреч
3. **Массовые действия** — выделить несколько клиентов → назначить чекап, отправить followup
4. **Поиск по всему** — глобальный поиск (клиенты, задачи, встречи, заметки)
5. **Экспорт отчётов** — PDF/Excel отчёт по клиенту (для руководства)

### Приоритет 2 (важно)
6. **Заметки к клиенту** — быстрые заметки/комментарии на странице клиента
7. **Шаблоны фолоуапов** — сохранённые шаблоны для типовых встреч
8. **История изменений** — таймлайн всех действий по клиенту (встречи, задачи, заметки)
9. **Уведомления в UI** — колокольчик с read/unread уведомлениями
10. **Комментарии к задачам** — обсуждение задач внутри системы

### Приоритет 3 (полезно)
11. **Дубликаты клиентов** — обнаружение и слияние дубликатов
12. **Автоматические напоминания** — push/email напоминания о встречах
13. **Рейтинг менеджеров** — leaderboard по KPI (чекапы, задачи, NPS)
14. **Интеграция с календарём** — Google Calendar / Outlook sync
15. **Голосовые заметки** — запись голосовых итогов встречи
16. **AI-рекомендации** — предложения по улучшению health score клиента
17. **Командная работа** — назначение задач другим менеджерам
18. **Мобильная версия** — адаптивный UI для телефона
19. **PWA** — установка как приложение на телефон
20. **Аудит изменений** — кто что менял и когда

### Приоритет 4 (долгосрочно)
21. **Автоматические QBR** — AI генерирует черновик QBR из данных
22. **Прогнозирование** — ML предсказание оттока клиента
23. **NPS опросы** — автоматические опросы удовлетворённости
24. **Интеграция с CRM** — Salesforce, HubSpot, Bitrix24
25. **Мультиязычность** — i18n для разных стран
