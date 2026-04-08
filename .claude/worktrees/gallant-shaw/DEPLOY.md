# Деплой AM Hub на Railway

## 1. Создать проект на Railway

1. Зайди на [railway.app](https://railway.app) → **New Project**
2. Выбери **Deploy from GitHub repo**
3. Подключи GitHub и выбери репо `Nicromo/AnyQueryAMHub`
4. Railway автоматически найдёт `am-hub-final/nixpacks.toml` и настроит сборку

---

## 2. Настроить Root Directory

В настройках сервиса:
- **Settings → Source** → **Root Directory** → укажи `am-hub-final`

---

## 3. Variables (переменные окружения)

В Railway → твой сервис → **Variables** → нажми **+ New Variable** для каждой:

| Переменная | Значение | Обязательно |
|---|---|---|
| `TG_BOT_TOKEN` | `8238040247:AAEAR-9ayQPfS3WspoZ56lS3HclRbfdj1C8` | ✅ |
| `TG_BOT_USERNAME` | `Nicromo` | ✅ |
| `ALLOWED_TG_IDS` | `124902915` | ✅ |
| `SECRET_KEY` | `aq-am-hub-prod-k9x2mZ7vQpLr4nJw` | ✅ |
| `MERCHRULES_API_URL` | `https://merchrules.any-platform.ru` | ✅ |
| `MERCHRULES_API_KEY` | *(оставь пустым, входи через ЛК)* | — |
| `SHEETS_SPREADSHEET_ID` | `1baqs2xGFZNxCuAwTfuDiE52KXIaLaKtuZzcSN4lce3M` | ✅ |
| `SHEETS_TOP50_GID` | `374545260` | ✅ |

> 💡 `RAILWAY_PUBLIC_DOMAIN` Railway задаёт автоматически — не нужно добавлять вручную.

---

## 4. Volume (постоянное хранилище для БД)

Railway не сохраняет файлы между деплоями без Volume. Чтобы данные не удалялись:

1. В проекте → **+ New** → **Volume**
2. Подключи к своему сервису
3. **Mount Path**: `/app/data`

После этого SQLite-база (`am_hub.db`) будет жить вечно.

---

## 5. После первого деплоя — зарегистрировать домен в Telegram

1. Открой [@BotFather](https://t.me/BotFather) → `/setdomain`
2. Выбери своего бота (`@Nicromo`)
3. Введи домен Railway (вида `am-hub-xxxxx.up.railway.app`)

Это нужно для Telegram Login Widget на странице входа.

---

## 6. Проверить

- Открой `https://am-hub-xxxxx.up.railway.app`
- Нажми **Войти через Telegram**
- Должна открыться главная страница с чекапами

---

## 7. Зарегистрировать webhook Telegram-бота

Webhook регистрируется **автоматически** при старте приложения (если задан `RAILWAY_PUBLIC_DOMAIN`).

Если что-то пошло не так — можно вручную:
```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://am-hub-xxxxx.up.railway.app/tg/webhook
```

---

## Команды бота

После деплоя напиши боту:
- `/help` — список команд
- `/checkups` — просроченные чекапы
- `/top50` — еженедельный Top-50 из Google Sheets
- `/top50m` — ежемесячный Top-50

---

## Обновление кода

```bash
git add -A
git commit -m "update"
git push origin main
```

Railway автоматически пересобирает и деплоит при каждом пуше в `main`.
