# Переменные окружения для Railway

Задай их в Railway → твой проект → Variables:

| Переменная | Значение |
|---|---|
| `TG_BOT_TOKEN` | `8238040247:AAEAR-9ayQPfS3WspoZ56lS3HclRbfdj1C8` |
| `TG_BOT_USERNAME` | `Nicromo` |
| `ALLOWED_TG_IDS` | `124902915` |
| `SECRET_KEY` | `aq-am-hub-prod-k9x2mZ7vQpLr4nJw` |
| `MERCHRULES_API_URL` | `https://merchrules.any-platform.ru` |
| `MERCHRULES_API_KEY` | _(получить у команды anyquery)_ |

> ⚠️ Этот файл НЕ пушится в git (добавь в .gitignore если надо).
> Храни токены только в Railway Variables, не в коде.

## После деплоя

Обязательно зарегистрируй домен в Telegram Login Widget:
1. Открой @BotFather → `/setdomain`
2. Выбери своего бота
3. Вставь домен Railway (например `am-hub-production.up.railway.app`)
