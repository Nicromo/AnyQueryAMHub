# Integrations Status — AM Hub

> Аудит на 21.04.2026 (после PR #104). Короткая сводка того, что реально работает,
> где двухсторонний обмен, где заглушки.

## TL;DR

| # | Интеграция | Pull | Push | Статус |
|---|---|:---:|:---:|:---:|
| 1 | Merchrules | ✅ | ✅ | ✅ |
| 2 | Airtable — клиенты | ✅ | ✅ | ✅ |
| 3 | Airtable — оплаты | ✅ | — | ⚠ только чтение |
| 4 | Ktalk | ✅ | — | ⚠ частично |
| 5 | Tbank Time (тикеты) | ✅ | — | ⚠ частично |
| 6 | Telegram Bot | ✅ | ✅ | ✅ |
| 7 | Google Sheets (Top-50, Churn) | ✅ | — | ⚠ read-only CSV |
| 8 | Groq / Qwen AI | ✅ | — | ✅ inference, ⚠ нет persist |
| 9 | Cloudflare R2 | ✅ | ✅ | ✅ |
| 10 | Diginetica (Search API) | ✅ | — | ✅ (после PR #105) |
| 11 | Chrome Extension ↔ Hub | ✅ | ✅ | ✅ |
| 12 | Email (SMTP) | — | — | ❌ `NotImplementedError` |

**Легенда**: ✅ работает · ⚠ частично · ❌ не работает · — не применимо.

---

## 1. Merchrules ⇄
**Pull** (`merchrules_sync.py`, `scheduler.job_sync_merchrules`):
- `POST /backend-v2/auth/login` — перебор полей email/login/username + json/form
- `GET /backend-v2/sites/{id}` — данные по указанным в `user.settings.merchrules.my_site_ids` (после PR #98)
- `GET /backend-v2/tasks?site_id=X` → `Task` upsert
- `GET /backend-v2/meetings?site_id=X&limit=100` → `Meeting` upsert по `external_id` (PR #96)
- `GET /backend-v2/analytics/top_queries|zero_queries|null_queries` → запросы для чекапа (PR #105)
- `GET /api/site/all` → Diginetica apiKey клиента (PR #105)

**Push** (`merchrules.py`):
- `POST /backend-v2/meetings` — встречи/follow-up с датой + summary + mood (PR #75)
- `POST /backend-v2/import/tasks/csv` — массовый экспорт задач

**Куда пишет в БД**: `Client.last_sync_at`, `Client.open_tickets`, `Task`, `Meeting`, `ClientSynonym`, `ClientWhitelistEntry`, `ClientBlacklistEntry`, `ClientMerchRule`.

**Проблемы**: auth-цепочка перебирает 8+ комбинаций путей × полей × форматов — на каждой ошибке логи растут.

---

## 2. Airtable — клиенты ⇄
**Source**: `appEAS1rPKpevoIel / tblIKAi1gcFayRJTn`

**Pull** (`airtable_sync.py`, cron `job_sync_airtable_clients`):
- `GET /v0/{base}/{table}?returnFieldsByFieldId=true` — по hardcoded field IDs
- Поля: `fldXeHkgIjzvr294Z` name, `fld0XMiWRh9xzvDy6` manager, `fldyvNxsQglqiQs48` segment, MRR, products, contacts
- Fallback: эвристика по названиям (`NAME_CANDIDATES`, `MANAGER_CANDIDATES`, etc.) — если база не та

**Push**:
- `PATCH /v0/{base}/{table}/{record_id}` — `FIELD_LAST_CONTACT`, `FIELD_STATUS_COMMENT` (после встречи)

**Куда пишет в БД**: `Client`, `ClientProduct`, `ClientContact`, `Client.airtable_record_id`.

**Особенности**:
- CSM-change detection (PR #98): при смене `manager_email` не перезаписываем автоматически, а создаём `Notification` + pending `ClientTransferRequest`
- Контакты **не удаляются** при синке (защита от потери ручных данных менеджера) — `airtable_sync.py:706-708`

---

## 3. Airtable — оплаты →
**Source**: `appEAS1rPKpevoIel / tblLEQYWypaYtAcp6 / viw977k6GUNrkeRRy`

**Pull** (`integrations/airtable_payments.py`, PR #97):
- `GET /v0/{base}/{table}?view=<view_id>` + пагинация
- Fuzzy-маппинг полей: `client/партнёр/сайт`, `сумма/amount`, `дата/срок/due`, `статус`, `менеджер`, `счёт`, `коммент`
- Фильтрация на клиенте по `manager_email / name`

**Push**: **нет** — финотдел ведёт таблицу руками, AM Hub только читает.

**Куда пишет в БД**: ничего; данные не сохраняются локально (читаются на каждый запрос `/api/me/payments-pending`).

**Fallback**: если Airtable недоступен → читаем `Client.payment_*` из БД (`?source=db`).

---

## 4. Ktalk →
**Pull** (`integrations/ktalk.py`):
- `GET /api/v1/spaces/{space}/events` — встречи за период, кэш 1ч
- `GET /.../events/{event_id}/transcript` — транскрипция
- `GET /.../events/{event_id}/recordings` — ссылки на запись

**Push**: нет.

**Куда пишет в БД**: `Meeting` upsert через `scheduler.job_sync_ktalk_meetings`.

**Проблемы**:
- `get_rooms()` — функция обрывается на половине (строка ~200)
- Transcript/recordings тянутся, но не сохраняются в `Meeting.transcript_url` / `Meeting.recording_url` регулярно

---

## 5. Tbank Time (тикеты) →
**Pull** (`integrations/tbank_time.py`, `ingest_tickets`):
- `GET /api/v4/teams/name/{team}/channels/name/{channel}` — ID канала
- `GET /api/v4/channels/{channel_id}/posts` — посты (тикеты)
- Парсинг по имени клиента: `_parse_post_as_ticket()`
- Кэш 5 минут

**Push**: нет.

**Куда пишет в БД**: `SupportTicket` + `Client.open_tickets`.

**Особенности**:
- Auto-sync после push time_token из расширения (PR #96)
- Ручной запуск: `POST /api/tickets/sync`

**Проблема**: `_parse_post_as_ticket` частично реализован, есть случаи когда тикет не мапится на клиента.

---

## 6. Telegram Bot ⇄
**Pull** (`tg_bot.py`):
- `POST /bot{token}/setWebhook` — подписка на апдейты
- Входящие: `/start`, `/help`, `/status`, `/inbox`, `/today`, `/top50`, `/checkups`, `/tasks <name>`, `/done <id>`, `/prep <name>`, `/client <name>`, `/renewal`, `/alert`

**Push** (`tg_notifications.py`):
- `POST /bot{token}/sendMessage` с HTML parse_mode
- События: `nps_incoming`, `deadline_soon`, `meeting_soon`, `sync_failed`, `client_transfer`, `daily_digest`, `morning_plan`

**Фильтр**: `ALLOWED_TG_IDS` env — whitelist пользователей.

---

## 7. Google Sheets →
**Source**:
- Top-50: `10SuYn0w2VyDU87KSrYE-A_TDqkekj7q__o910doRCsc`
- Churn/Downsell: `1Tkax6awhWmNXfXpzORPIqHy5qgAhLzfifSHc-YLQhhY`

**Pull** (`sheets_sync.py`, `sheets_top50.py`):
- Public CSV export: `GET /spreadsheets/d/{id}/export?format=csv`
- Парсинг: client name, type, MRR before/after, status, date → `UpsellEvent`

**Push**: нет (заглушка `sheets.py:615` — полная OAuth-интеграция не реализована).

**Требование**: таблица должна быть в режиме «Просмотр для всех по ссылке».

---

## 8. Groq / Qwen AI →
**Pull** (`ai_assistant.py`, `routers/ai.py`):
- `POST https://api.groq.com/openai/v1/chat/completions` — `llama-3.3-70b-versatile`
- Fallback: `POST https://dashscope.aliyuncs.com/.../chat/completions` — `qwen-plus`
- Groq Whisper для voice-notes: `/audio/transcriptions` (PR #76)

**Data grounding** (PR #100): в system prompt добавляется:
- Общий снапшот портфеля (clients, MRR, health, risk count)
- Детальный срез топ-40 клиентов (name, segment, MRR, health, дней без встречи, NPS, payment, tasks, tickets, contract_end)
- Per-client данные если `client_id` задан

**Что не сохраняется**: ответы AI пишутся только в `AIChat` history, но не индексируются / не переиспользуются в последующих промптах.

---

## 9. Cloudflare R2 ⇄
**Storage** (`storage.py`):
- `PUT s3.put_object` — загрузка файлов в `clients/{client_id}/{uuid}.{ext}`
- `GET s3.get_object` — скачивание
- Endpoint: `https://{CF_R2_ACCOUNT_ID}.r2.cloudflarestorage.com`

**Fallback**: `/tmp/amhub-uploads/` если R2 не сконфигурирован (но на Railway `/tmp` не персистентный — TODO `routers/files.py` про Railway Volume).

**Разрешённые MIME**: PDF, images, XLSX, audio/{webm,ogg,mp3,wav,mp4,m4a} (PR #76).

---

## 10. Diginetica (Search API) →
**Клиент**: Chrome extension (`static/amhub-ext/lib/diginetica.js`).

**Pull**: `GET {DIGINETICA_SEARCH_URL}?st={query}&apiKey=X&strategy=...`.

**ApiKey источник** (PR #105):
1. `Client.integration_metadata.diginetica_api_key` — если сохранён
2. Fallback: `GET https://merchrules.any-platform.ru/api/site/all` → находим apiKey по `site_id` → сохраняем в meta
3. Ручной ввод в расширении — если ничего нет

**Фикс в PR #105**: принимает кастомный URL, подставляет обязательные параметры если их нет, внятное сообщение об ошибке с телом ответа.

---

## 11. Chrome Extension ⇄ AM Hub
**Ext → Hub**:
- `POST /api/auth/tokens/push` — `time_token`, `ktalk_token` (PR #87 fix ключа)
- `POST /api/checkup/{cabinet_id}/results` — результаты чекапа
- `POST /api/checkup/{cabinet_id}/queries` — сохранить ручно введённые запросы (PR #105)
- `POST /api/sync/merchrules` — триггер ручного синка

**Hub → Ext**:
- `GET /api/extension/version` — текущая версия + download_url (PR #87 absolute URL)
- `GET /api/extension/download` — динамический ZIP, всегда свежий (PR #93)
- `GET /api/extension/config` — восстановление кредов (PR #92)
- `GET /api/checkup/{cabinet_id}/queries` — запросы: saved → Merchrules → last_result (PR #105)
- `GET /api/cabinets/{cabinet_id}` — данные кабинета + auto-fetch apiKey (PR #105)

**Storage persistence** (PR #92):
- `chrome.storage.sync` — синхронизируется через Google, переживает reinstall
- Fallback на `chrome.storage.local` + `/api/extension/config` restore

---

## 12. Email (SMTP) ❌
**Файл**: `email_service.py:48`
```python
raise NotImplementedError
```

Не реализовано. Всё email-подобное идёт через TG + inbox (`Notification` модель).

---

## Известные заглушки / TODO

| Файл:строка | Что |
|---|---|
| `routers/analytics.py:1637` | NPS send-survey — stub, пишет только в PartnerLog |
| `routers/checkups_mgmt.py:761` | `search-queries` endpoint — legacy заглушка (новый путь через `fetch_checkup_queries` работает в PR #105) |
| `integrations/airtable.py:266` | QBR календарь sync не реализован |
| `integrations/dashboard.py:245` | TODO: локальные данные |
| `qbr_auto_collect.py:46` | Per-product endpoints Merchrules неизвестны, используется эвристика |
| `sheets.py:615` | Google OAuth полная интеграция не сделана (только public CSV) |
| `routers/files.py` | Нужен Railway Volume для `/data/uploads` — сейчас `/tmp` не переживает рестарт |
| `routers/design.py:214` | Roadmap delete не проверяет author_id |
| `integrations/merchrules_extended.py:482` | TODO тестирование функций |
| `integrations/ktalk.py` `get_rooms()` | Функция обрывается |
| `integrations/tbank_time.py` `_parse_post_as_ticket` | Не всегда мапит пост на клиента |

## Рекомендации

1. **Если нужен SMTP email** — выбрать провайдера (Mailgun/Sendgrid/SES), заменить `NotImplementedError` на реальный send + шаблоны
2. **Railway Volume** — маунтить на `/data/uploads` чтобы voice-notes переживали рестарт (если R2 не настроен)
3. **Two-way sync Ktalk** — если нужна отправка результатов встречи обратно
4. **QBR календарь в Airtable** — текущий sync в `integrations/airtable.py:266` не реализован
5. **Google Sheets OAuth** — если нужен push (запись обратно в таблицы)
