# Переменные окружения для Railway

Задай в Railway → проект → **Variables**.

## Обязательные

| Переменная | Описание | Пример |
|---|---|---|
| `TG_BOT_TOKEN` | Токен Telegram-бота | `8238040247:AAE...` |
| `TG_BOT_USERNAME` | Username бота (без @) | `Nicromo` |
| `SECRET_KEY` | Любая случайная строка для сессий | `some-random-string-32chars` |
| `MERCHRULES_API_URL` | URL Merchrules | `https://merchrules.any-platform.ru` |

## Доступ (кто может войти)

| Переменная | Описание |
|---|---|
| `ALLOWED_TG_IDS` | Через запятую Telegram ID тех, кому разрешён вход. Если пусто — разрешён всем. |

Пример для команды из 3 человек:
```
ALLOWED_TG_IDS=124902915,987654321,111222333
```

> **Каждый менеджер сам вводит свои логин/пароль Merchrules в разделе Профиль после входа.**
> Кредсы хранятся в БД (SQLite на Railway Volume), привязаны к Telegram ID менеджера.

## Опциональные (глобальный fallback)

Если менеджер НЕ заполнил свой профиль — используются эти:

| Переменная | Описание |
|---|---|
| `MERCHRULES_LOGIN` | Логин Merchrules по умолчанию |
| `MERCHRULES_PASSWORD` | Пароль Merchrules по умолчанию |
| `TG_NOTIFY_CHAT_ID` | Chat ID для уведомлений по умолчанию |

## Google Sheets (Top-50)

| Переменная | Значение |
|---|---|
| `SHEETS_SPREADSHEET_ID` | ID таблицы Google Sheets |
| `SHEETS_TOP50_GID` | ID листа (gid в URL) |

## После деплоя

1. Открой @BotFather → `/setdomain`
2. Выбери своего бота
3. Вставь домен Railway (например `am-hub-production.up.railway.app`)

Это нужно для работы кнопки «Войти через Telegram» на странице логина.
