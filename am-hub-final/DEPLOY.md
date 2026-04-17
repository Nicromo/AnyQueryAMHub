# 🚀 Деплой AM Hub на Railway

> Версия: 2.0 | Обновлено: апрель 2026  
> Стек: FastAPI + PostgreSQL + Railway

---

## Шаг 1 — Форкнуть / подключить репозиторий

1. Зайди на [railway.app](https://railway.app) → **New Project**
2. Выбери **Deploy from GitHub repo**
3. Подключи репозиторий `Nicromo/AnyQueryAMHub`
4. ⚠️ **Root Directory** → укажи `am-hub-final`  
   *(Settings → Source → Root Directory)*

Railway автоматически найдёт `nixpacks.toml` и соберёт проект.

---

## Шаг 2 — Добавить PostgreSQL

1. В проекте нажми **+ New** → **Database** → **PostgreSQL**
2. Railway автоматически создаст БД и добавит переменную `DATABASE_URL` в сервис
3. Таблицы создаются автоматически при первом старте приложения — ничего делать не нужно

---

## Шаг 3 — Переменные окружения

Railway → твой сервис → **Variables**. Добавь по одной:

### 🔴 Обязательные

| Переменная | Пример / описание |
|---|---|
| `DATABASE_URL` | Задаётся автоматически PostgreSQL-плагином |
| `SECRET_KEY` | Любая случайная строка 32+ символа, например `am-hub-prod-k9x2mZ7vQpLr4nJw8sKe` |

> Сгенерировать SECRET_KEY: `python3 -c "import secrets; print(secrets.token_hex(32))"`

### 🟡 Merchrules (основные данные)

| Переменная | Описание |
|---|---|
| `MERCHRULES_LOGIN` | Email для входа в Merchrules |
| `MERCHRULES_PASSWORD` | Пароль Merchrules |
| `MERCHRULES_API_URL` | `https://merchrules.any-platform.ru` (по умолчанию) |

> После деплоя зайди на `/sync` → **«Диагностика авторизации»** — покажет точно работают ли креды.

### 🟡 Telegram Bot

| Переменная | Описание |
|---|---|
| `TG_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `ALLOWED_TG_IDS` | Telegram ID разрешённых пользователей через запятую, например `124902915,987654321` |

> Узнать свой Telegram ID: написать [@userinfobot](https://t.me/userinfobot)

### 🟡 AI-ассистент (prep, followup, риски)

| Переменная | Описание |
|---|---|
| `GROQ_API_KEY` | API ключ [console.groq.com](https://console.groq.com) — быстро, бесплатно |
| `QWEN_API_KEY` | API ключ DashScope — fallback, 1M токенов/мес бесплатно |

> Достаточно одного из двух. Groq рекомендован — бы