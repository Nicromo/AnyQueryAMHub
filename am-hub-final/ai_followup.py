"""
AI-обработка транскрипта встречи через Groq API.
Реализует логику скилла postmit-roadmap-approval:
  1. Постмит — клиентская версия (публичная)
  2. Постмит — внутренняя версия (риски, open questions, health)
  3. Нарезка roadmap-задач с правильными статусами и полями
  4. Структурированный JSON для предпросмотра перед загрузкой
"""
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", os.getenv("API_GROQ", ""))
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """Ты — CSM-аналитик компании AnyQuery. Разбираешь транскрипты встреч с партнёрами и формируешь:
1. Постмит в двух версиях (клиентская + внутренняя)
2. Список задач для Roadmap Dashboard

ПРАВИЛА ПОСТМИТА:
- Клиентская версия: аккуратно, без внутренней кухни, что договорились / что сделаем
- Внутренняя версия: задачи AnyQuery, риски, open questions, оценка здоровья аккаунта (🟢/🟡/🔴), настроение клиента

ПРАВИЛА ЗАДАЧ:
- 1 задача = 1 конкретное действие + ожидаемый результат
- Короткий операционный заголовок
- Описание: контекст "зачем"
- НЕ объединять несколько действий в одну задачу
- НЕ придумывать сроки, ответственных, договорённости — если не было в транскрипте, пиши [уточнить]

СТАТУСЫ (выбирать строго по контексту):
- plan — согласовано, но не стартовало ("обсудим", "запланируем", "сделаем")
- in_progress — уже началось ("делаем", "закинул коллегам", "разбираем", "уже в работе")
- done — явно завершено
- blocked — есть внешний блокер (доступы, ожидание от партнёра, лимиты)
- review — сделано, ждёт проверки/подтверждения

ДЕФОЛТЫ:
- status: plan (если из контекста неясно)
- priority: medium
- assignee: any (если задача AnyQuery) | partner (если задача партнёра)
- product: any_query_web (если не указано другое)

КОМАНДЫ (выбирать по контексту):
CS, DEV, ANALYTICS, PRODUCT, UXUI, TRACKING, LINGUISTS, FRONTEND, BACKEND, DATASCI, ANYRECS, ANYREVIEWS, Int, IMPROVE

Отвечай ТОЛЬКО валидным JSON без markdown-блоков и пояснений вне JSON."""

TASK_FIELDS = ["title", "description", "status", "priority", "team",
               "task_type", "assignee", "product", "link", "due_date"]

TASK_TYPES = ["tracking", "search_quality", "analytics", "data_science",
              "rnd", "integration", "marketing", "merchandising", "research"]


async def process_transcript(
    transcript: str,
    client_name: str,
    meeting_date: str,
) -> dict:
    """
    Обрабатывает транскрипт встречи через Groq.
    Возвращает:
    {
        "postmit_client": "...",   # версия для клиента
        "postmit_internal": "...", # внутренняя версия
        "tasks": [                 # список задач
            {
                "title": "...",
                "description": "...",
                "status": "plan|in_progress|done|blocked|review",
                "priority": "low|medium|high",
                "team": "...",
                "task_type": "...",
                "assignee": "any|partner",
                "product": "...",
                "link": "",
                "due_date": "",
                "status_reason": "фраза из транскрипта"
            }
        ],
        "health": "green|yellow|red",
        "mood": "positive|neutral|risk",
        "error": null
    }
    """
    if not GROQ_API_KEY:
        return {
            "postmit_client": "",
            "postmit_internal": "",
            "tasks": [],
            "health": "yellow",
            "mood": "neutral",
            "error": "GROQ_API_KEY не задан. Добавь переменную API_GROQ в Railway Variables.",
        }

    user_prompt = f"""Встреча: {client_name}
Дата: {meeting_date}

ТРАНСКРИПТ:
{transcript[:12000]}

Верни JSON строго по такому шаблону:
{{
  "postmit_client": "текст постмита для клиента",
  "postmit_internal": "внутренний постмит: задачи, риски, open questions, здоровье аккаунта",
  "health": "green или yellow или red",
  "mood": "positive или neutral или risk",
  "tasks": [
    {{
      "title": "короткий заголовок",
      "description": "контекст зачем",
      "status": "plan",
      "status_reason": "цитата из транскрипта",
      "priority": "medium",
      "team": "CS",
      "task_type": "analytics",
      "assignee": "any",
      "product": "any_query_web",
      "link": "",
      "due_date": ""
    }}
  ]
}}"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(GROQ_URL, headers=headers, json=payload)

            if resp.status_code == 429:
                import asyncio
                await asyncio.sleep(2 ** attempt)
                continue

            if resp.status_code != 200:
                return {
                    "error": f"Groq API вернул {resp.status_code}: {resp.text[:300]}",
                    "postmit_client": "", "postmit_internal": "",
                    "tasks": [], "health": "yellow", "mood": "neutral",
                }

            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Убираем возможные markdown-обёртки
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            result = json.loads(content)

            # Нормализуем поля задач
            for t in result.get("tasks", []):
                t.setdefault("status", "plan")
                t.setdefault("priority", "medium")
                t.setdefault("assignee", "any")
                t.setdefault("product", "any_query_web")
                t.setdefault("link", "")
                t.setdefault("due_date", "")
                t.setdefault("status_reason", "")
                # Статус только из допустимых значений
                if t["status"] not in ("plan", "in_progress", "done", "blocked", "review"):
                    t["status"] = "plan"

            result["error"] = None
            return result

        except json.JSONDecodeError as e:
            logger.error("JSON parse error from Groq: %s\nContent: %s", e, content[:500])
            return {
                "error": f"Не удалось разобрать ответ ИИ. Попробуй ещё раз.",
                "postmit_client": "", "postmit_internal": "",
                "tasks": [], "health": "yellow", "mood": "neutral",
            }
        except Exception as exc:
            logger.error("Groq exception (attempt %d): %s", attempt, exc)
            if attempt == 2:
                return {
                    "error": str(exc),
                    "postmit_client": "", "postmit_internal": "",
                    "tasks": [], "health": "yellow", "mood": "neutral",
                }

    return {
        "error": "Превышено количество попыток. Попробуй позже.",
        "postmit_client": "", "postmit_internal": "",
        "tasks": [], "health": "yellow", "mood": "neutral",
    }


async def _call_groq(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Универсальный вызов Groq API, возвращает строку ответа."""
    if not GROQ_API_KEY:
        return ""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(GROQ_URL, headers=headers, json=payload)
            if resp.status_code == 429:
                import asyncio
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                logger.error("Groq error %s: %s", resp.status_code, resp.text[:200])
                return ""
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.error("Groq call error (attempt %d): %s", attempt, exc)
    return ""


async def generate_pre_meeting_brief(
    client: dict,
    meetings: list[dict],
    open_tasks: list[dict],
    health: dict,
) -> str:
    """
    Генерирует краткий AI-бриф перед встречей.
    Возвращает отформатированный текст для Telegram.
    """
    system = (
        "Ты — ассистент аккаунт-менеджера. Твоя задача — подготовить краткий брифинг "
        "перед встречей с клиентом. Пиши по-русски, кратко, структурированно. "
        "Используй эмодзи для читаемости. Не придумывай факты — только то что есть в данных."
    )

    last_meeting = meetings[0] if meetings else None
    overdue_tasks = [t for t in open_tasks if t.get("due_date") and t["due_date"] < __import__("datetime").date.today().isoformat()]
    blocked_tasks = [t for t in open_tasks if t.get("status") == "blocked"]

    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
Health Score: {health.get('score', '?')}/100 ({health.get('color', 'yellow')})

Последняя встреча: {last_meeting['meeting_date'] if last_meeting else 'нет данных'}
Тип: {last_meeting['meeting_type'] if last_meeting else '-'}
Настроение: {last_meeting['mood'] if last_meeting else '-'}
Итог прошлой встречи: {(last_meeting.get('summary') or '')[:500] if last_meeting else 'нет данных'}

Открытых задач: {len(open_tasks)}
Просроченных: {len(overdue_tasks)}
Заблокированных: {len(blocked_tasks)}

Список задач:
{chr(10).join(f"- [{t.get('status','open')}] {t['text'][:80]} (дедлайн: {t.get('due_date','-')})" for t in open_tasks[:10])}

Составь брифинг:
1. Короткий статус клиента (1-2 предложения)
2. Что сделано с прошлой встречи (из задач)
3. Что зависло / заблокировано
4. 3 ключевых вопроса которые стоит задать сегодня
"""
    result = await _call_groq(system, user_prompt, max_tokens=1024)
    return result or "Не удалось сгенерировать бриф. Проверь GROQ_API_KEY."


async def generate_followup_draft(
    client: dict,
    meeting: dict,
    aq_tasks: list[dict],
    cl_tasks: list[dict],
    next_meeting: str = "",
) -> str:
    """Генерирует черновик фолоуап-сообщения клиенту."""
    system = (
        "Ты — аккаунт-менеджер компании AnyQuery. Пишешь фолоуап клиенту после встречи. "
        "Тон: дружелюбный, профессиональный. Используй структуру с эмодзи. "
        "Пиши от первого лица как AM. Не добавляй лишнего."
    )
    aq_list = "\n".join(f"• {t['text']}" + (f" (до {t.get('due_date', '?')})" if t.get("due_date") else "") for t in aq_tasks) or "—"
    cl_list = "\n".join(f"• {t['text']}" + (f" (до {t.get('due_date', '?')})" if t.get("due_date") else "") for t in cl_tasks) or "—"

    user_prompt = f"""Встреча с клиентом {client['name']} прошла {meeting.get('meeting_date', '')}.
Тип: {meeting.get('meeting_type', 'checkup')}
Краткий итог: {(meeting.get('summary') or '')[:400]}
Настроение встречи: {meeting.get('mood', 'neutral')}

Задачи AnyQuery:
{aq_list}

Задачи клиента:
{cl_list}

Следующая встреча: {next_meeting or 'не назначена'}

Напиши фолоуап-сообщение клиенту в TG. Структура:
- Короткое приветствие
- Благодарность за встречу
- Задачи AnyQuery (что мы берём)
- Задачи клиента (что нужно от них)
- Дата следующей встречи
- Закрытие
"""
    result = await _call_groq(system, user_prompt, max_tokens=800)
    return result or "Не удалось сгенерировать черновик."


async def extract_tasks_from_chat(text: str, client_name: str) -> list[dict]:
    """
    Извлекает договорённости и задачи из текста переписки.
    Возвращает список задач для создания.
    """
    system = (
        "Ты — помощник аккаунт-менеджера. Анализируешь текст переписки с клиентом "
        "и извлекаешь конкретные договорённости и задачи. "
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )
    user_prompt = f"""Клиент: {client_name}

Текст переписки:
{text[:3000]}

Извлеки все договорённости и задачи. Для каждой укажи:
- owner: "anyquery" (мы делаем) или "client" (клиент делает)
- text: текст задачи (кратко, конкретно)
- due_date: дата если упоминается (YYYY-MM-DD или пусто)

Верни JSON:
{{"tasks": [{{"owner": "anyquery", "text": "...", "due_date": ""}}, ...]}}

Если договорённостей нет — верни {{"tasks": []}}
"""
    raw = await _call_groq(system, user_prompt, max_tokens=1024)
    if not raw:
        return []
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return data.get("tasks", [])
    except Exception as e:
        logger.error("extract_tasks_from_chat JSON error: %s", e)
        return []


async def generate_client_recommendations(
    client: dict,
    meetings: list[dict],
    open_tasks: list[dict],
    knowledge_base: list[dict],
) -> list[dict]:
    """
    AI-рекомендации что улучшить у конкретного клиента.
    Возвращает список рекомендаций.
    """
    system = (
        "Ты — эксперт по e-commerce поиску компании AnyQuery. "
        "Анализируешь данные клиента и предлагаешь конкретные улучшения. "
        "Опирайся на базу знаний — что уже сработало у похожих клиентов. "
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )

    kb_text = "\n".join(
        f"- [{item['category']}] {item['title']}: {item['metric_result']}"
        for item in knowledge_base[:20]
    ) or "База знаний пуста"

    tasks_text = "\n".join(f"- {t['text']}" for t in open_tasks[:10]) or "нет"
    meetings_summary = f"Всего встреч: {len(meetings)}"
    if meetings:
        moods = [m.get("mood", "neutral") for m in meetings[:3]]
        meetings_summary += f", последние настроения: {', '.join(moods)}"

    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
{meetings_summary}
Открытых задач: {len(open_tasks)}
Текущие задачи:
{tasks_text}

База знаний (что работало у других):
{kb_text}

Предложи 3-5 конкретных улучшений для этого клиента.
Для каждого укажи:
- title: короткое название улучшения
- reason: почему именно это (на основе данных клиента)
- expected_result: ожидаемый результат
- priority: high/medium/low

Верни JSON:
{{"recommendations": [{{"title": "...", "reason": "...", "expected_result": "...", "priority": "medium"}}]}}
"""
    raw = await _call_groq(system, user_prompt, max_tokens=1200)
    if not raw:
        return []
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return data.get("recommendations", [])
    except Exception as e:
        logger.error("generate_recommendations JSON error: %s", e)
        return []


async def generate_qbr_report(
    client: dict,
    meetings: list[dict],
    done_tasks: list[dict],
    open_tasks: list[dict],
    quarter: str,
) -> dict:
    """
    Генерирует черновик QBR-отчёта.
    Возвращает структурированный dict с секциями.
    """
    system = (
        "Ты — аккаунт-менеджер AnyQuery. Составляешь QBR-отчёт за квартал. "
        "Пиши по-русски, структурированно, с конкретными данными. "
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )

    done_list = "\n".join(f"- {t['text']}" for t in done_tasks[:20]) or "нет закрытых задач"
    open_list = "\n".join(f"- [{t.get('status','open')}] {t['text']}" for t in open_tasks[:15]) or "нет открытых задач"
    meetings_info = "\n".join(
        f"- {m['meeting_date']} ({m['meeting_type']}): mood={m.get('mood','neutral')}"
        for m in meetings
    ) or "нет встреч"

    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
Квартал: {quarter}

Встречи за квартал:
{meetings_info}

Выполненные задачи ({len(done_tasks)}):
{done_list}

Открытые задачи ({len(open_tasks)}):
{open_list}

Составь QBR-отчёт. Верни JSON:
{{
  "summary": "2-3 предложения об итогах квартала",
  "achievements": ["достижение 1", "достижение 2", "достижение 3"],
  "not_done": ["что не успели и почему"],
  "q_next_priorities": ["приоритет 1 на следующий квартал", "приоритет 2", "приоритет 3"],
  "risks": ["риск 1 если есть"],
  "health_trend": "улучшение / без изменений / ухудшение"
}}
"""
    raw = await _call_groq(system, user_prompt, max_tokens=1500)
    if not raw:
        return {
            "error": "Не удалось сгенерировать QBR. Проверь GROQ_API_KEY.",
            "summary": "", "achievements": [], "not_done": [],
            "q_next_priorities": [], "risks": [], "health_trend": "—",
        }
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        data["error"] = None
        return data
    except Exception as e:
        logger.error("generate_qbr_report JSON error: %s", e)
        return {
            "error": f"Ошибка разбора ответа AI: {e}",
            "summary": raw[:500], "achievements": [], "not_done": [],
            "q_next_priorities": [], "risks": [], "health_trend": "—",
        }


async def generate_client_transfer_brief(
    client: dict,
    meetings: list[dict],
    open_tasks: list[dict],
    done_tasks: list[dict],
    health: dict,
) -> str:
    """
    Генерирует досье-передачу клиента новому AM.
    Возвращает структурированный текст для Telegram и распечатки.
    """
    system = (
        "Ты — старший аккаунт-менеджер AnyQuery. Готовишь досье-передачу клиента "
        "новому AM. Пиши по-русски, структурированно, без воды. "
        "Включи всё что нужно знать с первого дня работы с клиентом."
    )

    meetings_text = "\n".join(
        f"- {m['meeting_date']} ({m['meeting_type']}): mood={m.get('mood','neutral')}, {(m.get('summary') or '')[:100]}"
        for m in meetings[:10]
    ) or "нет встреч"

    open_list = "\n".join(f"- [{t.get('status','open')}] {t['text'][:80]}" for t in open_tasks[:10]) or "нет"
    done_list = "\n".join(f"- {t['text'][:80]}" for t in done_tasks[:5]) or "нет"

    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
Health Score: {health.get('score', '?')}/100 ({health.get('color', 'yellow')})
TG чат: {client.get('tg_chat_id', 'не указан')}
Примечания: {(client.get('notes') or 'нет')[:300]}

История встреч:
{meetings_text}

Открытые задачи ({len(open_tasks)}):
{open_list}

Недавно закрытые ({len(done_tasks)}):
{done_list}

Составь досье-передачу нового AM. Включи разделы:
1. 📋 Краткий профиль клиента
2. 🎯 Текущие приоритеты и задачи
3. ⚠️ Риски и болевые точки
4. 💡 Что работает, что нет
5. 📅 Что нужно сделать в первые 2 недели
6. 💬 Особенности коммуникации (тон, формат, предпочтения)
"""
    result = await _call_groq(system, user_prompt, max_tokens=1500)
    return result or "Не удалось сгенерировать досье. Проверь GROQ_API_KEY."


async def generate_benchmark_report(
    client: dict,
    health: dict,
    segment_median: int,
    segment_name: str,
) -> str:
    """
    Генерирует квартальный бенчмарк клиента vs медиана сегмента.
    Возвращает текст для отправки клиенту в TG.
    """
    system = (
        "Ты — аккаунт-менеджер AnyQuery. Пишешь клиенту квартальный бенчмарк-отчёт. "
        "Тон: позитивный, мотивирующий. Используй конкретные цифры. "
        "Пиши от первого лица (мы / команда AnyQuery)."
    )
    client_score = health.get("score", 0)
    diff = client_score - segment_median
    comparison = "выше медианы" if diff > 0 else ("ниже медианы" if diff < 0 else "на уровне медианы")

    user_prompt = f"""Клиент: {client['name']} (сегмент {segment_name})
Health Score клиента: {client_score}/100
Медиана сегмента {segment_name}: {segment_median}/100
Разница: {diff:+d} ({comparison})

Составь короткое сообщение клиенту (в TG) о его позиции среди похожих магазинов.
Включи:
- Позицию (лучше/хуже/наравне с другими)
- 1-2 позитивных момента
- 1-2 области для роста
- Призыв к действию (следующая встреча, конкретный шаг)

Не раскрывай имена других клиентов. Говори о "магазинах вашего сегмента"."""
    result = await _call_groq(system, user_prompt, max_tokens=600)
    return result or "Не удалось сгенерировать бенчмарк."


async def generate_platform_audit_tasks(
    client: dict,
    site_id: str,
    metrics_summary: str,
) -> list[dict]:
    """
    По данным аудита платформы генерирует список задач для исправления проблем.
    Возвращает список задач для создания в AM Hub.
    """
    system = (
        "Ты — аналитик платформы AnyQuery. Анализируешь данные аудита и формируешь "
        "конкретные задачи для улучшения качества поиска. "
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )
    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
Site ID: {site_id}

Результаты аудита:
{metrics_summary}

Сформируй список задач для исправления проблем.
Верни JSON:
{{"tasks": [
    {{"text": "конкретная задача", "priority": "high/medium/low", "team": "CS/DEV/ANALYTICS", "reason": "почему важно"}}
]}}

Создавай только задачи для реальных проблем. Не придумывай."""
    raw = await _call_groq(system, user_prompt, max_tokens=1000)
    if not raw:
        return []
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return data.get("tasks", [])
    except Exception as e:
        logger.error("generate_platform_audit_tasks JSON error: %s", e)
        return []


async def generate_anniversary_message(client: dict, years: int) -> str:
    """Ж3: Черновик поздравления клиента с годовщиной работы с AnyQuery."""
    system = (
        "Ты — аккаунт-менеджер AnyQuery. Пишешь тёплое, искреннее поздравление клиенту "
        "с годовщиной сотрудничества. Тон: дружелюбный, не шаблонный, без пафоса. "
        "Кратко, 3-4 предложения."
    )
    label = {1: "первый год", 2: "два года", 3: "три года", 5: "пять лет"}.get(years, f"{years} лет")
    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})
Годовщина: {label} вместе с AnyQuery

Напиши поздравительное сообщение в TG. Упомяни цифру (год/лет), поблагодари за доверие,
намекни на продолжение работы. Без emoji-спама. Подпись: команда AnyQuery."""
    result = await _call_groq(system, user_prompt, max_tokens=300)
    return result or f"🎉 {years} {'год' if years == 1 else 'года' if years < 5 else 'лет'} вместе — спасибо за доверие, {client['name']}! Продолжаем расти вместе 🚀\n\nС уважением, команда AnyQuery"


async def generate_welcome_message(client: dict) -> str:
    """Ж4: Приветственное сообщение для нового клиента."""
    system = (
        "Ты — аккаунт-менеджер AnyQuery. Пишешь первое приветственное сообщение новому клиенту в TG. "
        "Тон: тёплый, профессиональный, краткий. Дай понять что рядом и готов помочь."
    )
    user_prompt = f"""Новый клиент: {client['name']} (сегмент {client['segment']})

Напиши приветственное сообщение в TG-канал. Включи:
- Тёплое приветствие
- Кратко: кто мы и что делаем
- Как с нами связаться (AM всегда на связи)
- Следующий шаг (первая встреча)
Не более 5 предложений."""
    result = await _call_groq(system, user_prompt, max_tokens=300)
    return result or (
        f"👋 Добро пожаловать в AnyQuery, {client['name']}!\n\n"
        "Мы рады начать работу вместе. Ваш аккаунт-менеджер всегда на связи — "
        "пишите сюда с любыми вопросами.\n\n"
        "Первым шагом предлагаем провести онбординг-встречу — согласуем задачи и ритм работы. "
        "Когда вам удобно? 📅"
    )


async def prioritize_tasks_ai(tasks: list[dict], clients_context: list[dict]) -> list[dict]:
    """
    З2: AI-приоритизация задач на утро.
    Возвращает задачи с полем priority_rank и reason.
    """
    system = (
        "Ты — ассистент аккаунт-менеджера. Помогаешь расставить приоритеты задач на день. "
        "Учитывай: дедлайн, сегмент клиента (ENT важнее SS), риск (mood risk = важнее), "
        "заблокированные задачи, просроченность. Отвечай ТОЛЬКО валидным JSON."
    )
    tasks_text = "\n".join(
        f"ID:{t['id']} [{t.get('segment','?')}] {t['text'][:80]} "
        f"дедлайн:{t.get('due_date','-')} статус:{t.get('status','open')} клиент:{t.get('client_name','?')}"
        for t in tasks[:30]
    )
    user_prompt = f"""Задачи на сегодня:
{tasks_text}

Расставь приоритеты. Верни JSON:
{{"tasks": [{{"id": 123, "priority_rank": 1, "reason": "ENT клиент, просрочена"}}]}}
Сортируй от самого важного (1) к наименее важному."""

    raw = await _call_groq(system, user_prompt, max_tokens=800)
    if not raw:
        return tasks
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        rank_map = {item["id"]: item for item in data.get("tasks", [])}
        for t in tasks:
            info = rank_map.get(t["id"], {})
            t["priority_rank"] = info.get("priority_rank", 999)
            t["priority_reason"] = info.get("reason", "")
        return sorted(tasks, key=lambda x: x.get("priority_rank", 999))
    except Exception as e:
        logger.error("prioritize_tasks_ai error: %s", e)
        return tasks


async def generate_synonym_recommendations(
    client: dict,
    zero_results_queries: list[str],
    top_queries: list[str],
) -> list[dict]:
    """К2: AI-рекомендации синонимов на основе нулевых результатов."""
    system = (
        "Ты — эксперт по настройке поиска для e-commerce. Анализируешь запросы клиента "
        "и предлагаешь синонимы для улучшения качества поиска. "
        "Отвечай ТОЛЬКО валидным JSON."
    )
    zero_text = "\n".join(f"- {q}" for q in zero_results_queries[:20]) or "нет данных"
    top_text = "\n".join(f"- {q}" for q in top_queries[:20]) or "нет данных"

    user_prompt = f"""Клиент: {client['name']} (сегмент {client['segment']})

Запросы без результатов:
{zero_text}

Топ поисковых запросов:
{top_text}

Предложи синонимы которые помогут найти товары. Верни JSON:
{{"synonyms": [
  {{"query": "оригинальный запрос", "synonyms": ["вариант1", "вариант2"], "reason": "почему"}},
]}}"""
    raw = await _call_groq(system, user_prompt, max_tokens=800)
    if not raw:
        return []
    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip()).get("synonyms", [])
    except Exception as e:
        logger.error("generate_synonym_recommendations error: %s", e)
        return []


async def transcribe_audio(audio_url: str) -> str:
    """
    Транскрибирует аудио через OpenAI Whisper API.
    audio_url — URL публичного аудиофайла.
    Возвращает текст транскрипции.
    """
    import os
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        return ""

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            # Скачиваем аудио
            audio_resp = await client.get(audio_url)
            audio_data = audio_resp.content

            # Отправляем в Whisper
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {openai_key}"},
                files={"file": ("audio.mp3", audio_data, "audio/mpeg")},
                data={"model": "whisper-1", "language": "ru"},
            )
            if resp.status_code == 200:
                return resp.json().get("text", "")
            else:
                logger.error("Whisper API error %s: %s", resp.status_code, resp.text[:200])
                return ""
    except Exception as exc:
        logger.error("transcribe_audio error: %s", exc)
        return ""


async def run_post_meeting_pipeline(
    client: dict,
    meeting: dict,
    audio_url: str = "",
    transcript: str = "",
) -> dict:
    """
    Д1: Полный пайплайн после встречи.
    1. Транскрибация аудио (если есть URL)
    2. AI анализ транскрипта → постмит + задачи
    3. Возвращает результат для подтверждения AM

    Возвращает:
    {
        "transcript": "...",
        "postmit_client": "...",
        "postmit_internal": "...",
        "tasks": [...],
        "health": "green|yellow|red",
        "mood": "positive|neutral|risk",
        "error": null
    }
    """
    # Шаг 1: Транскрипция если нет готового текста
    if not transcript and audio_url:
        logger.info("Transcribing audio for meeting %d", meeting.get("id", 0))
        transcript = await transcribe_audio(audio_url)

    if not transcript:
        return {
            "transcript": "",
            "postmit_client": "",
            "postmit_internal": "",
            "tasks": [],
            "health": "yellow",
            "mood": "neutral",
            "error": "Нет транскрипта и не удалось транскрибировать аудио",
        }

    # Шаг 2: AI анализ транскрипта
    result = await process_transcript(
        transcript,
        client["name"],
        meeting.get("meeting_date", ""),
    )
    result["transcript"] = transcript
    return result


# ── Автотеги клиентов ─────────────────────────────────────────────────────────

async def generate_client_tags(client: dict, tasks: list[dict], meetings: list[dict]) -> list[str]:
    """
    AI генерирует 3-6 тегов для клиента на основе истории.
    Теги описывают характер клиента / текущее состояние.
    """
    segment = client.get("segment", "")
    health  = client.get("health_score", 0)
    risk    = client.get("risk_score", 0)
    name    = client.get("name", "")

    task_texts = "; ".join(t["text"][:60] for t in tasks[:10])
    meeting_moods = [m.get("mood", "neutral") for m in meetings[:5]]
    risk_cnt = sum(1 for m in meeting_moods if m == "risk")
    pos_cnt  = sum(1 for m in meeting_moods if m == "positive")

    prompt = f"""Клиент: {name} [{segment}], health={health}, risk={risk}
Последние задачи: {task_texts or 'нет'}
Настроения встреч (последние 5): {meeting_moods}

Сгенерируй от 3 до 6 коротких тегов для этого клиента (на русском, 1-3 слова каждый).
Теги описывают: характер отношений, текущий статус, возможности, риски.

Возможные теги (но не ограничивайся ими):
технический, лояльный, активный, сложный, растущий, в риске,
партнёр, новый, приоритет, уходит, нужна встреча, требует внимания,
потенциал апсела, стабильный, неактивный, промоутер

Ответь строго в формате JSON: {{"tags": ["тег1", "тег2", "тег3"]}}"""

    try:
        async with httpx.AsyncClient(timeout=30) as hx:
            resp = await hx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                    "max_tokens": 200,
                },
            )
        text = resp.json()["choices"][0]["message"]["content"].strip()
        import re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            tags = [str(t).strip()[:40] for t in data.get("tags", []) if t]
            return tags[:6]
    except Exception as exc:
        logger.warning("generate_client_tags error for %s: %s", name, exc)

    # Fallback: rule-based теги
    tags = []
    if risk > 60:
        tags.append("в риске")
    if health < 40:
        tags.append("требует внимания")
    if health > 75:
        tags.append("стабильный")
    if risk_cnt >= 2:
        tags.append("сложный")
    if pos_cnt >= 3:
        tags.append("лояльный")
    return tags or ["активный"]


# ── Апсел-сигналы ─────────────────────────────────────────────────────────────

async def detect_upsell_signals(
    client: dict,
    mr_data: dict,
    tasks: list[dict],
) -> list[dict]:
    """
    AI + rule-based детект апсел-возможностей.
    Возвращает список сигналов: [{type, details, confidence}]
    """
    signals = []
    name = client.get("name", "")
    segment = client.get("segment", "")
    health = client.get("health_score", 0)

    # Rule-based сигналы
    analytics = mr_data.get("analytics", {}) or {}
    search_vol = analytics.get("total_searches", 0)
    prev_vol   = analytics.get("prev_searches", 0)

    if prev_vol and search_vol > prev_vol * 1.3:
        signals.append({
            "type": "volume_growth",
            "details": f"Поиск вырос на {int((search_vol/prev_vol-1)*100)}% — возможно нужен более высокий тариф",
            "confidence": 0.85,
        })

    # Нет расширенной аналитики → предложить
    if not analytics.get("has_recommendations") and segment in ("ENT", "SME+"):
        signals.append({
            "type": "feature_gap",
            "details": "Клиент не использует персонализированные рекомендации — возможность допродажи AnyRecs",
            "confidence": 0.7,
        })

    # Индекс близок к лимиту
    index_size = analytics.get("index_size", 0)
    index_limit = analytics.get("index_limit", 0)
    if index_limit and index_size and index_size / index_limit > 0.85:
        signals.append({
            "type": "index_limit",
            "details": f"Индекс заполнен на {int(index_size/index_limit*100)}% — нужно расширение",
            "confidence": 0.9,
        })

    # Сегмент не соответствует объёму
    if segment in ("SME", "SMB") and search_vol > 500_000:
        signals.append({
            "type": "segment_mismatch",
            "details": f"Объём поиска ({search_vol:,}) соответствует ENT — обсудить апгрейд тарифа",
            "confidence": 0.75,
        })

    # Клиент лоялен и стабилен — время продавать доп. продукты
    if health > 70 and not any(s["type"] == "upsell_offer" for s in signals):
        open_task_texts = " ".join(t["text"].lower() for t in tasks[:20])
        if "рекоменда" not in open_task_texts and "апсел" not in open_task_texts:
            # AI проверяет контекст
            prompt = f"""Клиент {name} [{segment}], health={health}.
Задачи: {open_task_texts[:300] or 'нет активных задач'}
Есть ли возможность предложить дополнительные продукты или расширение? Ответь одним словом: да / нет"""
            try:
                async with httpx.AsyncClient(timeout=15) as hx:
                    resp = await hx.post(
                        GROQ_URL,
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": GROQ_MODEL,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2,
                            "max_tokens": 10,
                        },
                    )
                ans = resp.json()["choices"][0]["message"]["content"].strip().lower()
                if "да" in ans:
                    signals.append({
                        "type": "upsell_offer",
                        "details": f"Клиент стабилен (health={health}) — хороший момент для разговора о расширении",
                        "confidence": 0.6,
                    })
            except Exception:
                pass

    return signals
