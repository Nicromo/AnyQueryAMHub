# 🚀 Быстрый старт AM Hub - Integration Setup

## Что было сделано

Создана полная архитектура интеграций для AM Hub с модульными компонентами для работы с:
- ✅ Airtable (клиенты, менеджеры, QBR календарь)
- ✅ Merchrules (задачи, встречи, аналитика, чекапы)
- ⏳ Ktalk (встречи, записи, транскрипции)
- ⏳ Tbank Time (саппорт обращения)
- ⏳ Дашборд (двусторонняя синхронизация)

## Файлы проекта

```
am-hub-final/
├── integrations/                    # 🆕 Модули интеграций
│   ├── __init__.py
│   ├── airtable.py                 # ✅ Готов (get_clients, update_meeting_date)
│   ├── merchrules_extended.py      # ✅ Готов (analytics, checkups, tasks, meetings, feed)
│   ├── ktalk.py                    # ⏳ Структура (требует доработки)
│   ├── tbank_time.py               # ⏳ Структура (требует доработки)
│   └── dashboard.py                # ⏳ Структура (требует доработки)
│
├── models.py                        # 🔄 Расширена (новые поля, связи, CheckUp модель)
├── main.py                          # ⏳ Требует API endpoints
├── scheduler.py                     # ⏳ Требует расширения
└── .env.example                     # Переменные окружения
```

## Документация

- **INTEGRATIONS_GUIDE.md** - 📖 Примеры использования всех интеграций
- **INTEGRATION_ARCHITECTURE.md** - 🏗️ Архитектура модулей и структура
- **DATA_FLOW_ARCHITECTURE.md** - 📊 Диаграммы потоков данных и синхронизации

## Следующие шаги

### 1️⃣ Настройка переменных окружения

1. Копировать `.env.example` в `.env`
2. Заполнить значения:

```bash
# Airtable
AIRTABLE_TOKEN=patXXXXXXXXXXXXXX  # Создать в https://airtable.com/account/personal
AIRTABLE_BASE_ID=appEAS1rPKpevoIel
AIRTABLE_TABLE_ID=tblIKAi1gcFayRJTn
AIRTABLE_VIEW_ID=viwocTz78z44WlAu1

# Merchrules
MERCHRULES_API_URL=https://merchrules.any-platform.ru
MERCHRULES_LOGIN=your_login
MERCHRULES_PASSWORD=your_password

# Groq AI (для summary встреч)
GROQ_API_KEY=gsk_XXXXXXXXXXXX  # https://console.groq.com

# Остальные...
```

### 2️⃣ Проверить подключение Airtable

```bash
cd am-hub-final/

# Загрузить виртуальное окружение
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate     # Windows

# Установить зависимости
pip install -r requirements.txt

# Тестировать Airtable
python -m integrations.airtable
```

### 3️⃣ Добавить API endpoints в `main.py`

Пример:
```python
from integrations import airtable, merchrules
from models import Client
from database import SessionLocal

@app.get("/api/clients")
async def list_clients():
    """Получить список клиентов с аналитикой"""
    clients = await airtable.get_clients()
    
    result = []
    for client in clients:
        # Получить аналитику из Merchrules
        account_id = client.get('merchrules_account_id')
        analytics = await merchrules.fetch_account_analytics(account_id) if account_id else {}
        
        result.append({
            "id": client['id'],
            "name": client['name'],
            "manager": client['manager'],
            "segment": client['segment'],
            "health_score": analytics.get('health_score', 0),
            "revenue_trend": analytics.get('revenue_trend'),
        })
    
    return result

@app.get("/api/clients/{client_id}/checkups")
async def get_checkups(client_id: str):
    """Получить чекапы для клиента"""
    # Найти account_id в Airtable
    clients = await airtable.get_clients()
    client = next((c for c in clients if c['id'] == client_id), None)
    
    if not client:
        return {"error": "Client not found"}
    
    account_id = client.get('merchrules_account_id')
    if not account_id:
        return {"error": "Account ID not found"}
    
    checkups = await merchrules.fetch_checkups(account_id)
    return {"checkups": checkups}
```

### 4️⃣ Расширить `scheduler.py`

```python
from apscheduler.schedulers.background import BackgroundScheduler
from integrations import airtable, merchrules
from database import SessionLocal
from models import Client, SyncLog
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

async def sync_all_data():
    """Полная синхронизация: Airtable + Merchrules"""
    db = SessionLocal()
    sync_log = SyncLog(
        integration="airtable_merchrules",
        resource_type="all",
        action="sync",
        status="in_progress"
    )
    try:
        # 1. Получить клиентов из Airtable
        airtable_clients = await airtable.get_clients()
        logger.info(f"Loaded {len(airtable_clients)} clients from Airtable")
        
        # 2. Синхронизировать с БД
        for at_client in airtable_clients:
            # Попытаться найти в БД
            client = db.query(Client).filter_by(
                airtable_record_id=at_client['id']
            ).first()
            
            if not client:
                # Создать нового клиента
                client = Client(
                    airtable_record_id=at_client['id'],
                    name=at_client['name'],
                    manager_email=at_client['manager'],
                    segment=at_client['segment'],
                )
                db.add(client)
            else:
                # Обновить существующего
                client.name = at_client['name']
                client.manager_email = at_client['manager']
                client.segment = at_client['segment']
            
            db.flush()  # Получить ID
            
            # 3. Получить данные из Merchrules
            if client.merchrules_account_id:
                analytics = await merchrules.fetch_account_analytics(
                    client.merchrules_account_id
                )
                client.health_score = analytics.get('health_score', 0)
                client.revenue_trend = analytics.get('revenue_trend')
        
        db.commit()
        sync_log.status = "success"
        sync_log.records_processed = len(airtable_clients)
        logger.info("✅ Sync completed")
        
    except Exception as e:
        logger.error(f"❌ Sync error: {e}")
        db.rollback()
        sync_log.status = "error"
        sync_log.message = str(e)
    finally:
        db.add(sync_log)
        db.commit()
        db.close()

def run_async(coro):
    """Helper для запуска async функций из scheduler"""
    asyncio.run(coro)

# Регистрируем задачи
scheduler.add_job(
    run_async,
    "interval",
    hours=1,
    args=(sync_all_data(),),
    id="sync_all_data",
    name="Sync Airtable + Merchrules",
)

scheduler.start()
```

### 5️⃣ Обновить templates

Например, `workspace.html`:
```html
<!-- Добавить разделы с данными -->
<div class="dashboard">
    <!-- Клиенты -->
    <div class="clients-grid">
        <h2>Мои клиенты</h2>
        <div id="clients"></div>
    </div>
    
    <!-- Overdue Checkups (красным) -->
    <div class="checkups">
        <h2>⚠️ Чекапы просрочены</h2>
        <div id="overdue-checkups"></div>
    </div>
    
    <!-- Open tickets -->
    <div class="tickets">
        <h2>📞 Открытые обращения</h2>
        <div id="open-tickets"></div>
    </div>
    
    <!-- Recent meetings -->
    <div class="meetings">
        <h2>📹 Последние встречи</h2>
        <div id="recent-meetings"></div>
    </div>
</div>

<script>
// Загрузить клиентов
fetch('/api/clients')
    .then(r => r.json())
    .then(clients => {
        const html = clients
            .map(c => `
                <div class="client-card">
                    <h3>${c.name}</h3>
                    <p>Менеджер: ${c.manager}</p>
                    <p>Health Score: <strong>${c.health_score}</strong></p>
                    <p>Тренд: ${c.revenue_trend}</p>
                </div>
            `)
            .join('');
        document.getElementById('clients').innerHTML = html;
    });

// Загрузить чекапы
fetch('/api/checkups/overdue')
    .then(r => r.json())
    .then(checkups => {
        // ... отобразить с красным цветом
    });
</script>
```

## Важные замечания

### ⚠️ Merchrules Account ID

Для получения данных **аналитики и чекапов** требуется Account ID, а не just site_id!

```
GET /analytics/full?account_id=12345    ✅ Работает
GET /analytics/full?site_id=1967        ❌ Не работает

GET /checkups?account_id=12345          ✅ Работает
```

Account ID можно найти:
1. В Airtable колонке "номер аккаунта" (fieldId: fldreqkwkEXrEGGwg)
2. В Merchrules URL: `/analytics/full?account_id=<ID>`
3. Спросить маршрутизацию в системе

### 🔑 API Tokens

Нужно получить/создать:
1. **Airtable**: https://airtable.com/account/personal → Create Token
2. **Groq**: https://console.groq.com/keys → Create API Key
3. **Ktalk**: Спросить администратора (может не быть открытого API)
4. **Tbank Time**: Спросить администратора

### 📊 Кэширование

Все интеграции используют кэширование:
- Airtable: 15 минут
- Merchrules: 30 минут
- Ktalk: 1 час
- Tbank Time: 30 минут

### 🔄 Синхронизация

Текущие задачи в scheduler:
- ✅ Hourly sync (Airtable + Merchrules)
- ✅ Daily checkup alerts (08:00)
- ✅ Weekly digest (Friday 17:00)
- ⏳ Meeting reminders (every 30 min)
- ⏳ Ktalk sync (hourly)
- ⏳ Tbank Time sync (hourly)

## Тестирование

```bash
# Тест Airtable
python -m integrations.airtable

# Тест Merchrules
python -m integrations.merchrules_extended

# Запустить приложение
python -m uvicorn main:app --reload --port 8000

# Проверить эндпоинты
curl http://localhost:8000/api/clients
curl http://localhost:8000/health
```

## Полезные ссылки

- 📖 [Airtable API Docs](https://airtable.com/api)
- 📡 [Merchrules Dashboard](https://merchrules.any-platform.ru)
- 🤖 [Groq Console](https://console.groq.com)
- 🚂 [Railway.app Deploy](https://railway.app)

---

## Структура проекта (полная)

```
roadmap-bulk-tasks/
├── am-hub-final/                    # Production app
│   ├── integrations/                # 🆕 Интеграции
│   │   ├── airtable.py
│   │   ├── merchrules_extended.py
│   │   ├── ktalk.py
│   │   ├── tbank_time.py
│   │   ├── dashboard.py
│   │   └── __init__.py
│   ├── models.py                    # 🔄 Extended models
│   ├── main.py
│   ├── database.py
│   ├── scheduler.py                 # ⏳ Needs update
│   ├── auth.py
│   ├── templates/
│   ├── static/
│   ├── requirements.txt
│   └── .env.example                 # 🆕 Env template
│
├── am-hub/                          # Old version
├── INTEGRATION_ARCHITECTURE.md      # 🆕 Architecture
├── INTEGRATIONS_GUIDE.md            # 🆕 Usage guide
├── DATA_FLOW_ARCHITECTURE.md        # 🆕 Data flows
└── README.md
```

---

**Готово к использованию!** 🎉

Теперь можно постепенно:
1. Заполнить `.env`
2. Добавить endpoints в `main.py`
3. Расширить `scheduler.py`
4. Обновить templates
5. Тестировать интеграции
