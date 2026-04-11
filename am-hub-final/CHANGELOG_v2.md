# 🚀 AM Hub — Changelog v2.0

## Новые модули (Шаг 1: Интеграции)

### 1. `merchrules_client.py` — Расширенный клиент Merchrules
**Новые эндпоинты:**
- `/api/site/all` — список всех сайтов
- `/backend-v2/content` — задачи и контент
- `/api/custom-report` — кастомные отчеты
- `/api/report/agg/{site_id}/global` — агрегированная аналитика
- `/api/report/daily/{site_id}/global` — ежедневные метрики
- `/backend-v2/roadmap/comments/latest` — комментарии из роадмапа
- `/backend-v2/meetings/next` — следующая встреча
- `/checkups` — список чекапов
- `/feeds` — фиды
- `/feed-processing` — статус обработки фида
- `/search-settings` — настройки поиска

**Класс `MerchrulesClient`:**
- Метод `get_full_analytics()` — автоматически собирает все ключевые метрики за неделю
- Per-user авторизация с кэшированием токена (1 час)

---

### 2. `time_integration.py` — Интеграция с поддержкой Time
**Функции:**
- `get_channel_tickets()` — получение тикетов из канала `any-team-support`
- `get_critical_tickets()` — фильтрация критических обращений
- `search_tickets_by_account()` — поиск по названию аккаунта
- `get_account_support_summary()` — сводка по поддержке для клиента

**Поля для фильтрации:**
- `site_id` — ID сайта
- `account_name` — название аккаунта
- `status` — open, in_progress, resolved, closed
- `priority` — high, critical, urgent

**Environment переменные:**
```bash
TIME_API_URL=https://time.tbank.ru/tinkoff
TIME_API_TOKEN=your_token_here
```

---

### 3. `ktalk_helper.py` — Интеграция с видеовстречами Ktalk
**Функции:**
- `generate_meeting_link()` — создание ссылки на новую встречу с темой
- `search_artifacts()` — поиск записей встреч в `/content/artifacts`
- `get_recent_meetings()` — последние 5 встреч с клиентом
- `get_client_meetings_history()` — полная история + ссылка на новую встречу

**Быстрые ссылки:**
- Создание комнаты: `https://tbank.ktalk.ru/new?topic=Встреча с {client}`
- Поиск записей: `https://tbank.ktalk.ru/content/artifacts?q={client}`

---

### 4. `pre_call_brief.py` — Генератор брифов для встречи
**Функция `generate_pre_call_brief()`:**

Собирает данные из **всех источников**:
1. **Merchrules** — задачи, роадмап, фиды, аналитика, чекапы
2. **Time** — открытые и критические тикеты
3. **Ktalk** — история встреч
4. **Airtable** — QBR-события

**AI-рекомендации (Groq):**
- 3-5 ключевых рекомендаций для встречи
- Talking points (темы для обсуждения)
- Выделение рисков

**Форматирование для Telegram:**
- Функция `format_brief_for_telegram()` — HTML-разметка с эмодзи
- Автоматическая отправка пользователю в ЛС

**Пример использования:**
```python
brief = await generate_pre_call_brief(
    client_name="ООО Ромашка",
    site_id="17",
    manager_name="Иван",
    mr_login="ivan@company.com",
    mr_password="***",
    tg_id=123456789,
)
```

---

## Обновления в `main.py`

### Новые API эндпоинты:

#### `GET /prep/{client_id}/brief` (JSON)
Генерирует Pre-Call Brief для клиента.

**Ответ:**
```json
{
  "ok": true,
  "brief": {
    "client_name": "...",
    "sections": {
      "merchrules": {...},
      "support": {...},
      "meetings_history": {...},
      "qbr": {...}
    },
    "alerts": [...],
    "recommendations": [...],
    "talking_points": [...],
    "risks": [...],
    "quick_links": {...}
  }
}
```

#### `GET /prep/{client_id}/brief/telegram` (HTML)
Генерирует бриф и отправляет его пользователю в Telegram.

---

### Обновлена страница `/prep/{client_id}`
Добавлены **быстрые ссылки** на внешние сервисы:
- Merchrules Analytics
- Time Support (поиск по клиенту)
- Ktalk — новая встреча
- Ktalk — запись встреч

---

## Обновления в `requirements.txt`
Добавлена зависимость:
```txt
aiofiles>=23.0.0
```

---

## Что работает теперь:

✅ **Merchrules** — полная интеграция со всеми эндпоинтами  
✅ **Time** — просмотр тикетов поддержки прямо в карточке клиента  
✅ **Ktalk** — быстрое создание встреч и поиск записей  
✅ **AI-брифы** — автоматическая подготовка к встречам с рекомендациями  
✅ **Единая панель** — все данные в одном месте  

---

## Следующие шаги (План):

### Шаг 2: Доработка UI
- [ ] Виджет "Последние тикеты" на странице `/prep/{id}`
- [ ] Виджет "История встреч Ktalk" с прямыми ссылками на записи
- [ ] Кнопка "Сгенерировать бриф" с выводом AI-рекомендаций
- [ ] Индикатор статуса фида (🟢/🔴)

### Шаг 3: Уведомления
- [ ] Алёрт в Telegram при появлении критического тикета
- [ ] Ежедневный дайджест с просроченными чекапами и горящими тикетами

### Шаг 4: Календарь
- [ ] Объединение QBR (Airtable), чекапов (Merchrules) и встреч (БД)
- [ ] Виджет "Ближайшие события" на главной

---

## Настройка (Environment Variables)

Добавьте в Railway Variables или `.env`:

```bash
# Time (поддержка)
TIME_API_URL=https://time.tbank.ru/tinkoff
TIME_API_TOKEN=your_time_token

# Ktalk (видеовстречи)
KTALK_URL=https://tbank.ktalk.ru

# Merchrules (уже было)
MERCHRULES_API_URL=https://merchrules.any-platform.ru
MERCHRULES_LOGIN=your_login
MERCHRULES_PASSWORD=your_password

# Groq AI (для брифов)
GROQ_API_KEY=your_groq_key
```

---

## Тестирование

1. Запустите приложение:
   ```bash
   cd am-hub-final
   python main.py
   ```

2. Откройте `/prep/{client_id}` — проверьте быстрые ссылки

3. Нажмите "Генерировать бриф" (если есть кнопка) или вызовите:
   ```
   GET /prep/{client_id}/brief
   ```

4. Проверьте Telegram — должно прийти сообщение с брифом

---

**Дата обновления:** 2026-04-10  
**Версия:** 2.0.0
