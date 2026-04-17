"""
Qwen API через DashScope (Alibaba Cloud).
Бесплатный tier: 1M токенов/мес для qwen-plus.
Docs: https://dashscope.console.aliyun.com/
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen-plus")  # qwen-plus, qwen-turbo, qwen-max


def qwen_available() -> bool:
    """Проверить, что Qwen API key задан."""
    return bool(QWEN_API_KEY)


async def qwen_chat(
    system_prompt: str,
    user_message: str,
    model: str = None,
    max_tokens: int = 4000,
) -> str:
    """Отправить запрос к Qwen API."""
    if not QWEN_API_KEY:
        raise RuntimeError("QWEN_API_KEY not set")

    model = model or QWEN_MODEL
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                DASHSCOPE_API_URL,
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
                headers={
                    "Authorization": f"Bearer {QWEN_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        else:
            raise RuntimeError(f"Qwen API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        raise RuntimeError(f"Qwen API request failed: {e}")


async def qwen_generate_tasks(meeting_text: str) -> list:
    """Сгенерировать задачи из текста встречи."""
    prompt = f"""You are a Russian-language assistant. Extract tasks from the meeting text.
Respond ONLY with a valid JSON array of task objects.

Each task: {{"title": "emoji + action", "description": "1-2 sentences", "status": "plan", "priority": "medium", "assignee": "any" or "partner", "team": "", "task_type": ""}}

Rules:
- assignee="any" = Diginetica does it (analysis, tech work)
- assignee="partner" = partner does it (provide data, check on their side)
- For 4+ tasks, at least 1-2 must be assignee="partner"
- team/task_type: ONLY for assignee="any", empty for partner
- team options: LINGUISTS, ANALYTICS, TRACKING, DEV, BACKEND, DATASCI, CS
- task_type options: search_quality, analytics, tracking, data_science, merchandising, rnd

Meeting text:
{meeting_text[:8000]}"""

    response = await qwen_chat("", prompt, max_tokens=3000)

    # Parse JSON array from response
    import json, re
    raw = response.strip()
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if m:
        raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Qwen JSON parse error: {raw[:200]}")
        return []


async def qwen_generate_prep_brief(client_name: str, tasks: list, meetings: list) -> str:
    """Сгенерировать подготовку к встрече."""
    tasks_text = "\n".join([f"- {t.get('title', '')}" for t in tasks[:10]]) if tasks else "Нет открытых задач"
    meetings_text = "\n".join([f"- {m.get('title', m.get('type', ''))} ({m.get('date', '')})" for m in meetings[:5]]) if meetings else "Нет прошлых встреч"

    prompt = f"""Сделай краткую подготовку к встрече с клиентом {client_name}.

Открытые задачи:
{tasks_text}

Прошлые встречи:
{meetings_text}

Ответь структурированно:
1. Контекст (2-3 предложения)
2. Ключевые вопросы для обсуждения
3. Рекомендуемые действия"""

    return await qwen_chat("", prompt, max_tokens=1500)


async def qwen_generate_followup(client_name: str, meeting_notes: str) -> str:
    """Сгенерировать фолоуап после встречи."""
    prompt = f"""Сделай итоговый фолоуап после встречи с {client_name}.

Заметки со встречи:
{meeting_notes[:3000]}

Ответь в формате:
**Итоги встречи с {client_name}**

**Обсудили:**
1. ...
2. ...

**Дальнейшие шаги:**
- Мы: ...
- С вашей стороны: ..."""

    return await qwen_chat("", prompt, max_tokens=1500)
