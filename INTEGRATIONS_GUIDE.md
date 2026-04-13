# 📚 Руководство по использованию интеграций AM Hub

## 1. Airtable интеграция

### Получить список клиентов

```python
from integrations import airtable
import asyncio

async def load_clients():
    clients = await airtable.get_clients()
    for client in clients:
        print(f"{client['name']} - Менеджер: {client['manager']}")

asyncio.run(load_clients())
```

### Обновить дату встречи

```python
from integrations import airtable
from datetime import datetime

async def update_meeting():
    success = await airtable.update_meeting_date(
        record_id="rec12345",
        meeting_date=datetime.now(),
        comment="Встреча прошла успешно, discussed roadmap for Q2"
    )
    if success:
        print("✅ Встреча обновлена в Airtable")

asyncio.run(update_meeting())
```

**Настройка:**
- Создай Personal Access Token в Airtable: https://airtable.com/account/personal
- Скопируй значения в `.env`:
  ```
  AIRTABLE_TOKEN=
  AIRTABLE_BASE_ID=appEAS1rPKpevoIel
  AIRTABLE_TABLE_ID=tblIKAi1gcFayRJTn
  AIRTABLE_VIEW_ID=viwocTz78z44WlAu1
  ```

---

## 2. Merchrules Extended интеграция

### Получить аналитику аккаунта

```python
from integrations import merchrules

async def get_health_score():
    analytics = await merchrules.fetch_account_analytics(
        account_id="12345"
    )
    print(f"Health Score: {analytics.get('health_score')}")
    print(f"Revenue Trend: {analytics.get('revenue_trend')}")
    print(f"Open Tasks: {analytics.get('open_tasks_count')}")

asyncio.run(get_health_score())
```

### Получить чекапы

```python
from integrations import merchrules

async def check_overdue():
    checkups = await merchrules.fetch_checkups(account_id="12345")
    
    for checkup in checkups:
        if checkup['status'] == 'overdue':
            print(f"🚨 OVERDUE: {checkup['type']} - {checkup['date']}")

asyncio.run(check_overdue())
```

### Получить роадмап задачи

```python
from integrations import merchrules

async def get_tasks():
    tasks = await merchrules.fetch_roadmap_tasks(
        account_id="12345",
        status="plan,in_progress"
    )
    
    for task in tasks:
        print(f"[{task['priority']}] {task['title']}")

asyncio.run(get_tasks())
```

### Синхронизировать все данные аккаунта

```python
from integrations import merchrules

async def sync_account():
    data = await merchrules.sync_all_accounts_data(
        account_ids=["12345", "67890"]
    )
    
    for account_id, account_data in data.items():
        print(f"Account {account_id}:")
        print(f"  - Tasks: {len(account_data['tasks'])}")
        print(f"  - Meetings: {len(account_data['meetings'])}")
        print(f"  - Checkups: {len(account_data['checkups'])}")

asyncio.run(sync_account())
```

**Настройка:**
```
MERCHRULES_API_URL=https://merchrules.any-platform.ru
MERCHRULES_LOGIN=username
MERCHRULES_PASSWORD=password
```

---

## 3. Использование в APScheduler

### Пример в scheduler.py

```python
from apscheduler.schedulers.background import BackgroundScheduler
from integrations import airtable, merchrules
from database import SessionLocal
from models import Client
import asyncio

scheduler = BackgroundScheduler()

async def sync_all_clients():
    """Синхронизировать все клиенты (hourly)"""
    db = SessionLocal()
    try:
        # 1. Получить клиентов из Airtable
        airtable_clients = await airtable.get_clients()
        
        # 2. Для каждого получить данные из Merchrules
        for at_client in airtable_clients:
            merchrules_account_id = at_client.get('merchrules_account_id')
            if not merchrules_account_id:
                continue
            
            # Получить аналитику
            analytics = await merchrules.fetch_account_analytics(merchrules_account_id)
            
            # Обновить в БД
            db_client = db.query(Client).filter_by(
                airtable_record_id=at_client['id']
            ).first()
            
            if db_client:
                db_client.health_score = analytics.get('health_score', 0)
                db_client.revenue_trend = analytics.get('revenue_trend')
                db_client.last_sync_at = datetime.now()
        
        db.commit()
        print("✅ Sync completed")
    except Exception as e:
        print(f"❌ Sync error: {e}")
        db.rollback()
    finally:
        db.close()

def run_async_task(coro):
    """Helper для запуска async функций из scheduler"""
    asyncio.run(coro)

# Регистрируем задачи
scheduler.add_job(
    run_async_task,
    "interval",
    hours=1,
    args=(sync_all_clients(),),
    id="sync_all_clients"
)

scheduler.start()
```

---

## 4. Dashboard отправка обновлений

### Синхронизировать изменения задачи

```python
from integrations import dashboard

async def update_task_in_dashboard(task_id, new_status):
    # Добавить в очередь
    await dashboard.sync_manager.queue_update(
        resource="tasks",
        action="update",
        data={
            "id": task_id,
            "status": new_status,
        }
    )
    
    # Отправить все накопленные обновления
    success = await dashboard.sync_manager.flush_updates("tasks")
    
    if success:
        print("✅ Обновления отправлены в дашборд")

asyncio.run(update_task_in_dashboard("task_123", "done"))
```

---

## 5. Обработка встреч из Ktalk + AI summary

### План реализации:

```python
from integrations import ktalk, merchrules
from integrations.dashboard import sync_manager
from groq import Groq

client = Groq()

async def process_meeting(meeting_id):
    # 1. Получить транскрипцию
    transcript = await ktalk.get_meeting_transcript(meeting_id)
    if not transcript:
        return
    
    # 2. Генерировать summary с AI
    summary_prompt = f"""
    Проанализируй эту транскрипцию встречи и создай краткое резюме на русском языке.
    Выдели ключевые обсуждаемые вопросы, решения и задачи которые нужно выполнить.
    
    Транскрипция:
    {transcript['text']}
    
    Ответ в формате:
    ## Основные темы
    - ...
    
    ## Решения
    - ...
    
    ## Задачи
    - ...
    """
    
    response = client.chat.completions.create(
        messages=[{"role": "user", "content": summary_prompt}],
        model="mixtral-8x7b-32768",
    )
    
    summary = response.choices[0].message.content
    
    # 3. Сохранить встречу в БД
    db = SessionLocal()
    meeting = Meeting(
        merchrules_id=meeting_id,
        transcript=transcript['text'],
        summary=summary,
    )
    db.add(meeting)
    db.commit()
    
    # 4. Отправить в дашборд
    await sync_manager.queue_update(
        resource="meetings",
        action="create",
        data={
            "id": str(meeting.id),
            "summary": summary,
        }
    )
    
    return meeting
```

---

## 6. Переменные окружения (Railway.app)

В Railway добавь все переменные из `.env.example`:

```
DATABASE_URL=postgresql://...
AIRTABLE_TOKEN=...
MERCHRULES_LOGIN=...
MERCHRULES_PASSWORD=...
...
```

Можно загрузить сразу несколько:

```bash
railway up --set DATABASE_URL=... --set AIRTABLE_TOKEN=...
```

---

## 7. Debugging

### Логирование

```python
import logging

# Настроить логирование для интеграций
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("integrations")

# Теперь все юег.info/warning/error будут выводиться
```

### Тестирование интеграций

```bash
# В корне am-hub-final/

# Тест Airtable
python -m integrations.airtable

# Тест Merchrules
python -m integrations.merchrules_extended

# Тест Ktalk
python -m integrations.ktalk

# Тест Tbank Time
python -m integrations.tbank_time
```

---

## Что дальше?

1. **Добавить API endpoints** в `main.py` для управления:
   - GET /api/clients - список клиентов с аналитикой
   - GET /api/clients/<id>/tasks - задачи клиента
   - GET /api/clients/<id>/checkups - чекапы
   - POST /api/meetings - создать встречу
   - Etc.

2. **Обновить templates** для отображения:
   - Health Score для каждого клиента
   - Overdue checkups (красным)
   - Open support tickets
   - Recent meetings с summary
   - Roadmap tasks

3. **Расширить scheduler.py**:
   - Синхронизировать встречи из Ktalk
   - Загружать support tickets из Time
   - Генерировать AI summary
   - Отправлять уведомления

4. **Добавить dashboard двустороннюю синхронизацию** (опционально):
   - PULL обновления из дашборда
   - Синхронизировать статусы задач
   - Отправлять изменения обратно

---

## Полезные ссылки

- Airtable API: https://airtable.com/app/api/docs
- Merchrules (ваша система): https://merchrules.any-platform.ru/api
- Groq API: https://console.groq.com/docs/quickstart
- Railway.app: https://railway.app/dashboard
