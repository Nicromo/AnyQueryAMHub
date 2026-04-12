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
