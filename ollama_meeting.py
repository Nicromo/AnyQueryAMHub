#!/usr/bin/env python3
"""
Перевод итогов встречи с партнёром в список задач для дашборда (Roadmap).
Использует Grok API (xAI) для генерации задач в формате, готовом к импорту.
"""
import json
import logging
import os
import re
import threading

import requests as _requests

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Глобальный cancel-event; сбрасывается перед каждой генерацией
_cancel_event = threading.Event()


def _get_api_key(username: str | None = None) -> str:
    """Получить Groq API key: env → per-user creds → global creds."""
    for env_var in ("API_GROQ", "GROQ_API_KEY"):
        key = os.environ.get(env_var, "").strip()
        if key:
            return key
    try:
        from creds import load_grok_api_key
        return load_grok_api_key(username=username) or ""
    except Exception:
        return ""


def cancel_generation() -> None:
    """Сигнал прерывания текущей генерации."""
    _cancel_event.set()


def _reset_cancel() -> None:
    _cancel_event.clear()


def grok_available(username: str | None = None) -> bool:
    """Проверить, что Groq API key задан (для конкретного пользователя или глобально)."""
    return bool(_get_api_key(username=username))


def get_model_name() -> str:
    """Вернуть имя используемой модели."""
    return GROQ_MODEL

# Команды и направления для подстановки по контексту (как просил пользователь)
TEAM_BY_CONTEXT = [
    # Data Science — в приоритете, т.к. специфические ключевые слова
    ("выпрямитель", "DATASCI"),
    ("дописыватель", "DATASCI"),
    ("предиктор", "DATASCI"),
    ("транслитерац", "DATASCI"),
    ("data science", "DATASCI"),
    ("дата сайнс", "DATASCI"),
    ("ml ", "DATASCI"),
    ("нейросет", "DATASCI"),
    # Рекомендации
    ("рекомендац", "ANYRECS"),
    ("рекс", "ANYRECS"),
    # Отзывы
    ("отзыв", "ANYREVIEWS"),
    # Трекинг
    ("трекинг", "TRACKING"),
    ("трек", "TRACKING"),
    ("событи", "TRACKING"),
    # Доработки/улучшения
    ("доработ", "IMPROVE"),
    ("улучшен", "IMPROVE"),
    ("импров", "IMPROVE"),
    # Аналитика
    ("аналитик", "ANALYTICS"),
    ("конверси", "ANALYTICS"),
    ("метрик", "ANALYTICS"),
    ("отчёт", "ANALYTICS"),
    ("дашборд", "ANALYTICS"),
    ("данн", "ANALYTICS"),
    # Поиск/лингвисты
    ("ранжирован", "LINGUISTS"),
    ("лингвист", "LINGUISTS"),
    ("релевантность", "LINGUISTS"),
    ("синоним", "LINGUISTS"),
    ("коррекц", "LINGUISTS"),
    ("поисков", "LINGUISTS"),
    ("поиск", "LINGUISTS"),
    # Разработка / Backend
    ("интеграц", "DEV"),
    ("api", "BACKEND"),
    ("бэкенд", "BACKEND"),
    # CS
    ("клиентск", "CS"),
    ("cs", "CS"),
]

TASK_TYPE_BY_CONTEXT = [
    ("трекинг", "tracking"),
    ("трек", "tracking"),
    ("событи", "tracking"),
    ("выпрямитель", "data_science"),
    ("дописыватель", "data_science"),
    ("предиктор", "data_science"),
    ("транслитерац", "data_science"),
    ("нейросет", "data_science"),
    ("ml ", "data_science"),
    ("рекомендац", "merchandising"),
    ("рекс", "merchandising"),
    ("ранжирован", "search_quality"),
    ("релевантность", "search_quality"),
    ("лингвист", "search_quality"),
    ("коррекц", "search_quality"),
    ("поисков", "search_quality"),
    ("поиск", "search_quality"),
    ("аналитик", "analytics"),
    ("конверси", "analytics"),
    ("метрик", "analytics"),
    ("данн", "analytics"),
    ("исследова", "research"),
]


MEETING_TO_TASKS_PROMPT = """You are a Russian-language assistant. Respond ONLY with a valid JSON array. No markdown, no explanation, no headers — ONLY JSON.

Extract tasks from the meeting text. All text values (title, description) MUST be in Russian. Maximum 6 tasks.

Rules for each task:
- title: ONE relevant emoji + space + action verb in infinitive, short. Examples: "🔍 Проверить роботизированные запросы", "📊 Сравнить конверсию в приложении", "⚙️ Настроить трекинг событий", "📋 Согласовать список коррекций", "🔗 Интегрировать API", "📈 Проанализировать метрики", "🤖 Настроить ML-модель"
- description: 1-2 concrete sentences with details
- status: "plan" by default
- priority: "medium" by default
- team: ONLY for assignee="any". One of — LINGUISTS, ANALYTICS, TRACKING, IMPROVE, DATASCI, ANYRECS, DEV, BACKEND, CS, PRODUCT — or empty. For partner tasks: team MUST be empty string "". NEVER use "integration" as team or task_type.
- task_type: ONLY for assignee="any". One of: search_quality, analytics, tracking, research, data_science, merchandising, rnd — or empty. For partner tasks: task_type MUST be empty string "". NEVER use "integration".
- status rules:
  * "plan" — task not started yet (default)
  * "in_progress" — already in progress: "уже начали", "ведётся", "продолжаем", "в процессе", "уже делаем" → in_progress
  * "discussion" — being discussed, no clear action yet
- assignee rules (VERY IMPORTANT):
  * "any" = Diginetica does it — analysis, search tuning, our platform technical work
  * "partner" = partner does it — providing data, checking on their side, clarifying their config, sharing access
  * With 4+ tasks, at LEAST 1-2 must be assignee="partner". Never assign ALL to "any".
  * "уточнить", "предоставить", "поделиться данными", "проверить у себя", "согласовать" → partner
  * Partner analytics/conversion checks that only they can do → partner
- product, due_date, link: empty string unless explicitly mentioned

Example:
[{"title":"Проверить роботизированные запросы","description":"Проанализировать долю многословных запросов с миксом языков в поиске Супер Аптеки.","status":"plan","priority":"medium","team":"ANALYTICS","task_type":"analytics","assignee":"any","product":"","due_date":"","link":""},{"title":"Предоставить данные по конверсии в приложении","description":"Выгрузить и прислать метрики конверсии в мобильном приложении по брендам Супер Аптека и Столетов.","status":"plan","priority":"medium","team":"ANALYTICS","task_type":"analytics","assignee":"partner","product":"","due_date":"","link":""}]

Now output the JSON array for the following meeting text:"""


# Шаг 1: саммаризация транскрипции в структурированный markdown-саммари (product-менеджер стиль).
# На выходе — только саммари. Из него потом нарезаются задачи и постмит (шаг 2).
SUMMARY_PROMPT_TEMPLATE = """Ты — опытный product-менеджер и аналитик интеграций поисковых/рекомендательных систем. Твоя задача — сделать очень качественное, лаконичное и структурированное саммари регулярной встречи по продукту на русском языке.

Обязательная структура и стиль:

**Саммари звонка «[название встречи из первого предложения или заголовка]» [дата из текста]**

**Основные темы и итоги:**

Разбей обсуждение на логические блоки (3–8 штук), каждый с подзаголовком в стиле:

### 1. [Краткое название темы, например: Фильтры в поиске]

- Кратко: суть проблемы / что обсуждали
- Что уже улучшено / сделано недавно
- Текущие проблемы / расхождения / примеры
- Договорённости / планы / что дальше
- (если есть — конкретные примеры запросов или ссылок)

Делай блоки по важности и объёму обсуждения. Самые большие и проблемные темы — в начало.

**Ключевые действия:**

- [Исполнитель, например: Елена] → [конкретное действие, что именно сделать]
- [Исполнитель] → [действие]
- ...

Пиши максимально кратко, по делу, без воды, без «угу», «секунду», технических помех и повторов. Только суть, факты и договорённости.

Используй markdown:
- **жирный** для важных моментов
- списки и подзаголовки для читаемости
- не больше 1–2 предложений на пункт внутри блока

Если в тексте есть названия продуктов/компаний — оставляй их как есть."""

# Шаг 2: из готового саммари извлечь post_meeting_message и tasks для дашборда. Ответ — только JSON.
EXTRACT_FROM_SUMMARY_PROMPT = """По готовому саммари встречи (markdown ниже) сформируй два блока для дашборда.

1) post_meeting_message — краткое сообщение в чат после звонка:
Коллеги, спасибо за встречу!
Обсудили:
1. [тема]
2. [тема]
Дальнейшие шаги:
1. Мы — [действие]
2. С вашей стороны — [действие]
Без имён: вместо имён «мы» или «С вашей стороны».

2) tasks — массив задач из «Ключевые действия» и договорённостей саммари.
- title: ONE relevant emoji + space + глагол-инфинитив. Примеры: "📊 Предоставить данные по конверсии", "🔍 Проверить роботизированные запросы", "⚙️ Настроить трекинг", "📋 Согласовать список", "🔗 Интегрировать API", "📈 Проанализировать метрики"
- description: 1–2 предложения с деталями
- status: «plan» / «in_progress» / «discussion»
- priority: «medium»
- team: ТОЛЬКО для assignee="any" — LINGUISTS / ANALYTICS / TRACKING / IMPROVE / DATASCI / ANYRECS / DEV / BACKEND / CS / PRODUCT. Для partner: team="" (пусто). НИКОГДА не используй "integration" как team или task_type.
- task_type: ТОЛЬКО для assignee="any" — search_quality / analytics / tracking / research / data_science / merchandising / rnd. Для partner: task_type="" (пусто). НИКОГДА "integration".
- status:
  * "plan" — задача не начата (по умолчанию)
  * "in_progress" — если из текста понятно, что работа уже началась: "уже ведётся", "в процессе", "продолжаем", "уже начали"
  * "discussion" — обсуждается, нет конкретного действия
- assignee (ОЧЕНЬ ВАЖНО):
  * «any» = Diginetica: анализ, настройка поиска, технические работы
  * «partner» = партнёр: предоставить данные, проверить у себя, уточнить настройки, поделиться доступом
  * Если задач 4+, минимум 1-2 должны быть assignee="partner". НЕ ставь всё на «any».
  * «уточнить», «предоставить», «поделиться», «согласовать», «проверить со своей стороны» → partner.

Ответь ТОЛЬКО валидным JSON-объектом, без markdown — только JSON от { до }:
{"post_meeting_message":"...","tasks":[{"title":"...","description":"...","status":"plan","priority":"medium","team":"ANALYTICS","task_type":"analytics","assignee":"any","product":"","due_date":"","link":""},...]}"""

# Оставлен для обратной совместимости (например, конфиг с transcription_prompt); при двухшаговом потоке не используется.
TRANSCRIPTION_PROMPT_TEMPLATE = """Ты — помощник, который составляет итоги деловой встречи на русском языке.
Тебе дан структурированный бриф встречи: задачи нашей стороны, задачи партнёра и темы встречи.

Ответь ТОЛЬКО валидным JSON-объектом. Без markdown, без пояснений — только JSON начиная с { и заканчивая }.
ВСЕ текстовые значения — ТОЛЬКО на русском языке.

═══════════════════════════════════════
ПРАВИЛА ДЛЯ summary (САМОЕ ВАЖНОЕ):
═══════════════════════════════════════
- Пиши 3–6 тезисов. Каждый тезис = одна тема встречи.
- СОХРАНЯЙ технические термины дословно: A/B тест, nDCG, API, rerank, трекинг, персонализация, и т.п.
- Пиши конкретно: НЕ "обсудили рекомендации", а "договорились запустить A/B тест персонализированного ранжирования в вебе".
- Глаголы: "Договорились о...", "Разобрали...", "Решили...", "Выяснили, что...", "Запланировали...".
- Если есть срок — указывай: "вернёмся на этой неделе", "до конца квартала".
- НЕ начинай тезисы со слова "Обсуждение".

═══════════════════════════════════════
ПРАВИЛА ДЛЯ tasks:
═══════════════════════════════════════
- tasks[].assignee: задачи "ЗАДАЧИ НАШЕЙ СТОРОНЫ" → assignee="any"; "ЗАДАЧИ ПАРТНЁРА" → assignee="partner". НИКОГДА не путай.
- tasks[].title — действие-инфинитив. НЕ "Планировать X", НЕ "Обсудить Y".
- tasks[].description — 1–2 предложения с деталями. НЕ ПИШИ "как указано в обсуждении".
- tasks[].status — "plan" / "in_progress" / "discussion".
- Создай задачи для ВСЕХ пунктов из ЗАДАЧИ НАШЕЙ СТОРОНЫ и ЗАДАЧИ ПАРТНЁРА (до 7 задач).

═══════════════════════════════════════
ПРАВИЛА ДЛЯ post_meeting_message:
═══════════════════════════════════════
- Начинается с "Коллеги, спасибо за встречу!"
- В "Обсудили:" — краткие темы (3–5 штук)
- В "Дальнейшие шаги:": "Мы — [действие]" для наших, "С вашей стороны — [действие]" для партнёра
- Имён нет.

Структура JSON:
{"summary":"1. [тезис]\\n2. [тезис]","post_meeting_message":"Коллеги, спасибо за встречу!\\n\\nОбсудили:\\n1. [тема]\\n\\nДальнейшие шаги:\\n1. Мы — [действие]","tasks":[{"title":"...","description":"...","status":"plan","priority":"medium","team":"TRACKING","task_type":"integration","assignee":"any","product":"","due_date":"","link":""}]}

Теперь сформируй JSON для следующего брифа встречи:"""


def _grok_chat(
    system_prompt: str,
    user_message: str,
    format_json: bool = True,
    cancel_event: threading.Event | None = None,
    api_key: str | None = None,
) -> str:
    """Отправить запрос к Groq API.

    format_json: при True просим JSON-ответ; при False — свободный текст (markdown).
    api_key: если передан — использовать его, иначе из env/creds-файла.
    """
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Генерация отменена пользователем")
    if not api_key:
        api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Groq API key не задан. Добавьте API_GROQ в env или укажите в настройках."
        )
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})
    payload: dict = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if format_json:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = _requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _infer_team_and_type(title: str, description: str) -> tuple[str, str]:
    """По заголовку и описанию подставить team и task_type если пусто."""
    text = (title + " " + description).lower()
    team = ""
    task_type = ""
    for kw, t in TEAM_BY_CONTEXT:
        if kw in text:
            team = t
            break
    for kw, tt in TASK_TYPE_BY_CONTEXT:
        if kw in text:
            task_type = tt
            break
    return team, task_type


def _parse_json_from_response(raw: str) -> list[dict]:
    """Достать JSON-массив из ответа модели (может быть обёрнут в ```json ... ```)."""
    raw = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        raw = m.group(1).strip()
    start = raw.find("[")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "[":
                depth += 1
            elif raw[i] == "]":
                depth -= 1
                if depth == 0:
                    raw = raw[start : i + 1]
                    break
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return [data] if isinstance(data, dict) else []
    except json.JSONDecodeError as e:
        logger.warning("JSON parse error: %s, raw snippet: %s", e, raw[:500])
        return []


def _normalize_task(t: dict) -> dict | None:
    """Привести задачу к формату дашборда, подставить дефолты и вывести team/task_type по контексту."""
    title = (t.get("title") or "").strip()
    if not title:
        return None
    desc = (t.get("description") or "").strip()
    status = (t.get("status") or "").strip() or "plan"
    priority = (t.get("priority") or "").strip() or "medium"
    team = (t.get("team") or "").strip()
    task_type = (t.get("task_type") or "").strip()
    assignee = (t.get("assignee") or "").strip() or "any"
    if assignee.lower() == "partner":
        assignee = "partner"
    else:
        assignee = "any"
    product = (t.get("product") or "").strip()
    due_date = (t.get("due_date") or "").strip()
    link = (t.get("link") or "").strip()
    # Для задач партнёра — команда не указывается (это их внутренняя структура)
    if assignee == "partner":
        team = ""
        task_type = ""
    elif not team or not task_type:
        inferred_team, inferred_type = _infer_team_and_type(title, desc)
        if not team:
            team = inferred_team
        if not task_type:
            task_type = inferred_type
    return {
        "title": title,
        "description": desc,
        "status": status,
        "priority": priority,
        "team": team,
        "task_type": task_type,
        "assignee": assignee,
        "product": product,
        "due_date": due_date,
        "link": link,
    }


def meeting_text_to_tasks(
    text: str,
    model: str | None = None,
    host: str | None = None,
    prompt_prefix: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """
    По тексту итогов встречи сгенерировать список задач через Groq API.
    Возвращает список словарей в формате полей задачи дашборда.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) > 12000:
        text = text[:12000] + "\n[... текст обрезан ...]"
    system = (prompt_prefix or "").strip() + MEETING_TO_TASKS_PROMPT.strip()
    user = text
    logger.info("Groq generate: model=%s text_len=%s", GROQ_MODEL, len(text))
    _reset_cancel()
    try:
        response = _grok_chat(system, user, format_json=True, cancel_event=_cancel_event, api_key=api_key)
    except Exception as e:
        raise RuntimeError(f"Groq API ошибка: {e}") from e
    if _cancel_event.is_set():
        raise RuntimeError("Генерация отменена пользователем")
    tasks_raw = _parse_json_from_response(response)
    result = []
    for t in tasks_raw:
        if isinstance(t, dict):
            norm = _normalize_task(t)
            if norm:
                result.append(norm)
    logger.info("Parsed %s tasks from meeting text", len(result))
    return result


# —— Транскрипция звонка (формат: первая строка — заголовок, далее HH:MM:SS\tИмя\tТекст) ——

def parse_transcription_metadata(raw_text: str) -> dict:
    """
    Парсит транскрипцию. Возвращает:
    - title: первая строка (заголовок)
    - lines: список {timestamp, speaker, text}
    - speakers: уникальные имена спикеров (по порядку появления)
    - suggested_partner_side, suggested_our_side: подсказки из названия (до/после " & " или " | ")
    """
    raw = (raw_text or "").strip()
    lines_list = []
    speakers_order = []
    seen = set()
    title = ""
    suggested_partner_side = ""
    suggested_our_side = ""
    for i, line in enumerate(raw.splitlines()):
        line = line.rstrip()
        if not line:
            continue
        if i == 0:
            title = line
            for sep in (" & ", " | ", " и ", " – ", " - "):
                if sep in title:
                    parts = title.split(sep, 1)
                    if len(parts) == 2:
                        a, b = parts[0].strip(), parts[1].strip()
                        for c in ('"', "'", "»", "«"):
                            a, b = a.strip(c).strip(), b.strip(c).strip()
                        # Вытащить короткое название: из первой части — в кавычках или последний фрагмент (бренд)
                        if "[" in a and "]" in a:
                            m = re.search(r"\[.*?\]\s*([^\"]+)", a)
                            if m:
                                a = m.group(1).strip()
                        elif '"' in a:
                            m = re.search(r'"([^"]+)"', a)
                            if m:
                                a = m.group(1).strip()
                        if not a:
                            a = parts[0].strip().split()[-1] if parts[0].strip() else ""
                        # Вторая часть: взять первое слово (часто Any / компания)
                        b_clean = b.split("|")[0].strip().split()[0] if b else ""
                        if b_clean:
                            b = b_clean
                        suggested_partner_side, suggested_our_side = a, b
                        break
            continue
        parts = line.split("\t", 2)
        if len(parts) >= 3:
            ts, speaker, text = parts[0].strip(), parts[1].strip(), (parts[2] if len(parts) > 2 else "").strip()
            if speaker and (ts or text):
                lines_list.append({"timestamp": ts, "speaker": speaker, "text": text})
                if speaker not in seen:
                    seen.add(speaker)
                    speakers_order.append(speaker)
        elif len(parts) == 2 and ":" in parts[0]:
            ts, rest = parts[0].strip(), parts[1].strip()
            sp = rest.split(None, 1)
            speaker = sp[0] if sp else ""
            text = sp[1] if len(sp) > 1 else ""
            if speaker:
                lines_list.append({"timestamp": ts, "speaker": speaker, "text": text})
                if speaker not in seen:
                    seen.add(speaker)
                    speakers_order.append(speaker)
    return {
        "title": title,
        "lines": lines_list,
        "speakers": speakers_order,
        "suggested_partner_side": suggested_partner_side,
        "suggested_our_side": suggested_our_side,
    }


def annotate_transcription(lines: list, partner_speakers: list) -> str:
    """Превращает список строк транскрипции в текст с метками [Партнёр] / [Any]."""
    partner_set = {s.strip().lower() for s in partner_speakers if s and str(s).strip()}
    out = []
    for row in lines:
        speaker = (row.get("speaker") or "").strip()
        text = (row.get("text") or "").strip()
        if not text and not speaker:
            continue
        label = "[Партнёр]" if speaker.lower() in partner_set else "[Any]"
        out.append(f"{label} {speaker}: {text}")
    return "\n".join(out)


def _is_garbled(text: str) -> bool:
    """Признак сильно искажённой ASR-строки (артефакт распознавания речи)."""
    garbled_rx = re.compile(
        r'\b(дск[уа]|эрай|эрой|дисиджи|гарифм|дисижд)\b', re.IGNORECASE
    )
    if garbled_rx.search(text):
        return True
    words = text.split()
    if len(words) >= 5:
        short = sum(1 for w in words if len(re.sub(r'[^а-яёА-ЯЁa-zA-Z]', '', w)) < 3)
        if short / len(words) > 0.55:
            return True
    return False


def _extract_commitment_phrase(text: str) -> str | None:
    """
    Из строки транскрипции вытаскивает только суть обязательства.
    Возвращает None если суть не найдена или строка — ASR-мусор.
    """
    if _is_garbled(text):
        return None
    sentences = re.split(r'(?<=[.!?])\s+|(?<=,)\s+(?=[А-ЯЁ])', text)
    action_rx = re.compile(
        r"\b(я\s+(отправлю|скину|пришлю|вышлю|сделаю|подготовлю|добавлю|зафиксирую|созвонюсь|напишу|покажу|продублирую|вернусь|поделюсь|поделюся|скажу|расскажу|предоставлю)|"
        r"(мы|с нашей стороны)\s+(отправим|скинем|пришлём|сделаем|подготовим|добавим|зафиксируем|проверим|встретимся|созвонимся|проведём|начнём|пошарим)|"
        r"(с вашей стороны|на вашей стороне)\s+.{0,50}|"
        r"я\s+(добавлю|вернусь|оценим|посмотрю|встретимся|расскажу|предоставлю))",
        re.IGNORECASE,
    )
    for s in sentences:
        s = s.strip()
        if 8 < len(s) < 200 and action_rx.search(s) and not _is_garbled(s):
            return s
    if action_rx.search(text) and len(text) > 8 and not _is_garbled(text):
        return text[:100]
    return None


def build_structured_brief(annotated: str) -> str:
    """
    Программно извлекает обязательства [Any] и [Партнёр] + темы встречи.
    Возвращает краткий структурированный briefing для модели.
    """
    lines = [l.strip() for l in annotated.split("\n") if l.strip()]

    # Паттерны первого лица = говорящий сам обязуется
    first_person_rx = re.compile(
        r"\b(я\s+(отправлю|скину|пришлю|вышлю|сделаю|подготовлю|добавлю|зафиксирую|созвонюсь|напишу|покажу|продублирую|могу (скинуть|отправить|продублировать|добавить|показать|поделиться))|"
        r"(мы|с нашей стороны)\s+(отправим|скинем|пришлём|сделаем|подготовим|добавим|зафиксируем|проверим|встретимся|созвонимся|проведём|начнём|пошарим))",
        re.IGNORECASE,
    )
    partner_commits_rx = re.compile(
        r"\bя\b.{0,25}?\b(добавлю|скину|вернусь|оценим|проведём|созвонюсь|посмотрю|пришлю|вышлю|встретимся|предоставлю|поделюсь|спрошу)\b",
        re.IGNORECASE,
    )
    delegate_to_partner_rx = re.compile(
        r"\b(с вашей стороны|на вашей стороне)\b",
        re.IGNORECASE,
    )
    delegate_to_us_rx = re.compile(
        r"\b(вам\s+(?:\S+\s+){0,3}нужно\s+|вам (созвониться|скинуть|отправить|сверить|обсудить|встретиться))\b",
        re.IGNORECASE,
    )

    # Ключевые слова тем встречи
    _MEETING_TOPIC_KW = [
        "рекоменд", "персонализированн", "персональн",
        "разметк", "мобильн", "трекинг", "событи", "события",
        "абэ тест", "ab тест", "интеграц", "клик",
        "сортировк", "документац", "синхронизац",
        "метрик", "итог", "план", "квартал",
        "поиск", "выдач", "релевантн",
        "корзин", "заказ",
    ]

    # Паттерн для выделения предложений — более агрессивный
    _sent_split_rx = re.compile(r'(?<=[.!?])\s+|(?<=\.)\s+(?=[А-ЯЁA-Z])')

    any_tasks = []
    partner_tasks = []
    topic_candidates = []   # (score, text)

    for line in lines:
        is_any = line.startswith("[Any]")
        is_partner = line.startswith("[Партнёр]")
        text = line.split(": ", 1)[1].strip() if ": " in line else line
        if len(text) < 10:
            continue

        # Анализ на уровне предложений — одна длинная строка может дать
        # и any-задачу ("я отправлю"), и partner-задачу ("с вашей стороны...")
        sentences = [s.strip() for s in _sent_split_rx.split(text) if s.strip()]
        if not sentences:
            sentences = [text]

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 8 or _is_garbled(sent):
                continue
            is_question = sent.endswith("?")

            if is_any:
                if first_person_rx.search(sent):
                    any_tasks.append(sent[:140])
                if not is_question and delegate_to_partner_rx.search(sent):
                    partner_tasks.append(sent[:140])

            if is_partner:
                if not is_question and partner_commits_rx.search(sent):
                    partner_tasks.append(sent[:140])
                if delegate_to_us_rx.search(sent) and not is_question:
                    any_tasks.append(f"[нас просят сделать это, assignee=any] {sent[:120]}")

        if len(text) > 80:
            text_lower = text.lower()
            score = sum(1 for kw in _MEETING_TOPIC_KW if kw in text_lower)
            if score > 0:
                topic_candidates.append((score, text))

    # Дедупликация (убираем почти одинаковые)
    def dedup(lst):
        seen = []
        for item in lst:
            if not any(item[:40] in x or x[:40] in item for x in seen):
                seen.append(item)
        return seen

    any_tasks = dedup(any_tasks)
    partner_tasks = dedup(partner_tasks)

    # Топ тем — по релевантности + стратифицированно по всей встрече
    topic_candidates.sort(key=lambda x: -x[0])
    # Берём топ-15 по релевантности, сортируем обратно по порядку в тексте
    top_topics = [tc[1] for tc in topic_candidates[:15]]
    sampled_topics = [t[:120] for t in top_topics[:8]]

    def clean_phrase(p: str) -> str:
        """Убирает хвостовые условные клаузы, имена в обращениях и лишние частицы."""
        # Убираем ", если у меня ..." и подобное
        p = re.sub(r',?\s*если у меня.{0,60}$', '', p, flags=re.IGNORECASE).strip()
        # Убираем финальный "я" — артефакт "Я пришлю я."
        p = re.sub(r'\s+я\s*\.$', '.', p, flags=re.IGNORECASE).strip()
        # Убираем слишком длинные вводные
        p = re.sub(r'^(Ну,?\s+|Вот,?\s+|Ладно,?\s+|Хорошо,?\s+|тогда\s+)', '', p, flags=re.IGNORECASE).strip()
        # Убираем обращения вида "Настя, если есть возможность ..." → оставляем суть
        p = re.sub(r'^[А-ЯЁ][а-яё]+,\s+', '', p).strip()
        # Убираем "я бы, действительно, может быть, ..." → суть начинается с глагола
        p = re.sub(r'^я бы,?\s+действительно,?\s+может быть,?\s+', 'я могу ', p, flags=re.IGNORECASE).strip()
        return p

    any_tasks = [clean_phrase(t) for t in any_tasks if clean_phrase(t)]
    partner_tasks = [clean_phrase(t) for t in partner_tasks if clean_phrase(t)]

    lines_out = []
    if any_tasks:
        lines_out.append("ЗАДАЧИ НАШЕЙ СТОРОНЫ (assignee=\"any\" в JSON):")
        for t in any_tasks[:5]:
            lines_out.append(f"  - {t}")
    if partner_tasks:
        lines_out.append("\nЗАДАЧИ ПАРТНЁРА (assignee=\"partner\" в JSON):")
        for t in partner_tasks[:5]:
            lines_out.append(f"  - {t}")
    if sampled_topics:
        lines_out.append("\nТЕМЫ ВСТРЕЧИ (для summary и post_meeting_message):")
        for t in sampled_topics:
            lines_out.append(f"  - {t}")

    brief = "\n".join(lines_out)
    if len(brief) > 4000:
        brief = brief[:4000] + "\n[...]"
    return brief


# Сильные ключевые слова: явные обязательства / решения
_STRONG_ACTION_KW = [
    "отправлю", "пришлю", "вышлю", "скину", "сегодня отправлю", "сегодня скину",
    "сделаю", "сделаем", "подготовим", "подготовлю", "подготовила",
    "договорил", "договорились", "согласовали", "зафиксирую", "зафиксируем",
    "созвонимся", "встретимся", "запланируем", "проведём", "обсудим",
    "добавлю", "добавим", "поставим", "проверим",
    "решили", "мяч на", "начнём с", "начнём с веб",
    "во 2-м квартале", "следующей неделе", "следующем квартале",
    "нужно провести", "нужно сделать", "нужно добавить",
    "давайте сделаем", "давайте обсудим", "давайте встретимся",
    "пошарить", "предоставим", "предоставить доступ",
]

# Более слабые — только для контекста (темы)
_TOPIC_KW = [
    "рекомендации", "персонализированная сортировка", "персональная сортировка",
    "трекинг", "разметка", "мобильное приложение", "документация",
    "абэ тест", "a/b тест", "веб", "интеграция", "трафик",
    "итоги", "план", "цель", "фокус",
]


def extract_key_moments(annotated: str, max_chars: int = 7000) -> str:
    """
    Извлекает строки с явными решениями/договорённостями из всего текста.
    Стратифицированная выборка — берёт из каждой трети встречи, не только из начала.
    """
    lines = [l for l in annotated.split("\n") if l.strip()]
    strong_kw = [kw.lower() for kw in _STRONG_ACTION_KW]
    topic_kw = [kw.lower() for kw in _TOPIC_KW]
    n = len(lines)

    def line_score(line: str) -> int:
        """Выше = важнее."""
        line_lower = line.lower()
        text_part = line.split(": ", 1)[1] if ": " in line else line
        if len(text_part.strip()) < 30:
            return 0
        score = 0
        score += sum(2 for kw in strong_kw if kw in line_lower)
        score += sum(1 for kw in topic_kw if kw in line_lower)
        return score

    # Разбиваем на трети и из каждой берём топ строки
    third = max(1, n // 3)
    thirds = [
        (0, third),
        (third, 2 * third),
        (2 * third, n),
    ]

    # Квота на треть: пропорционально max_chars
    quota_per_third = max_chars // 3

    selected_parts = []
    for start, end in thirds:
        chunk = lines[start:end]
        scored = [(line_score(line), i, line) for i, line in enumerate(chunk)]
        scored = [(s, i, l) for s, i, l in scored if s > 0]
        scored.sort(key=lambda x: (-x[0], x[1]))  # по убыванию важности, но сохраняем порядок

        # Берём лучшие строки из этой трети, но не больше квоты символов
        picked_indices = set()
        chars_used = 0
        for score, idx, line in scored:
            if chars_used + len(line) + 1 > quota_per_third:
                break
            picked_indices.add(start + idx)
            chars_used += len(line) + 1

        # Восстанавливаем исходный порядок и добавляем контекст (предыдущую строку)
        ordered = sorted(picked_indices)
        context_indices = set()
        for idx in ordered:
            context_indices.add(idx)
            if idx > 0 and len(lines[idx - 1]) > 60:
                context_indices.add(idx - 1)
        ordered_with_context = sorted(context_indices)
        selected_parts.append("\n".join(lines[i] for i in ordered_with_context))

    # Склеиваем три части с разделителем
    result = "\n\n[--- следующий блок встречи ---]\n\n".join(p for p in selected_parts if p)

    if len(result) > max_chars:
        result = result[:max_chars] + "\n[...]"

    return result


def smart_compress_transcript(annotated: str, max_chars: int = 14000) -> str:
    """
    Умная компрессия: всегда включает начало и конец,
    из середины — только строки с ключевыми словами.
    """
    if len(annotated) <= max_chars:
        return annotated

    all_lines = annotated.split("\n")
    n = len(all_lines)
    head_count = max(5, n // 7)
    tail_count = max(5, n // 7)
    head_lines = all_lines[:head_count]
    tail_lines = all_lines[n - tail_count:]
    middle_lines = all_lines[head_count: n - tail_count]

    strong_kw = [kw.lower() for kw in _STRONG_ACTION_KW]
    topic_kw = [kw.lower() for kw in _TOPIC_KW]
    all_kw = strong_kw + topic_kw
    important_middle = [
        line for line in middle_lines
        if len(line) > 60 and any(kw in line.lower() for kw in all_kw)
    ]

    parts = ["\n".join(head_lines)]
    if important_middle:
        parts.append("\n[...]\n" + "\n".join(important_middle))
    parts.append("\n[...]\n" + "\n".join(tail_lines))
    result = "\n".join(parts)

    if len(result) > max_chars:
        half = max_chars // 3
        head_text = "\n".join(head_lines)[:half]
        tail_text = "\n".join(tail_lines)[-half:]
        kw_text = "\n".join(important_middle)[: max_chars - len(head_text) - len(tail_text) - 50]
        result = head_text + "\n\n[...важные моменты...]\n" + kw_text + "\n\n[...конец встречи...]\n" + tail_text

    return result


def _parse_transcription_json_response(raw: str) -> dict:
    raw = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        raw = m.group(1).strip()
    start = raw.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    raw = raw[start : i + 1]
                    break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def process_transcription(
    raw_text: str,
    partner_speakers: list,
    model: str | None = None,
    host: str | None = None,
    prompt_prefix: str | None = None,
    api_key: str | None = None,
) -> dict:
    """
    Обработка транскрипции: сначала саммари (структурированный markdown), затем из саммари — постмит и задачи.
    partner_speakers — имена участников со стороны партнёра.
    Возвращает: { summary, post_meeting_message, tasks } или { error }.
    """
    meta = parse_transcription_metadata(raw_text)
    lines = meta.get("lines") or []
    if not lines:
        return {"summary": "", "post_meeting_message": "", "tasks": [], "error": "Нет строк с участниками"}
    annotated = annotate_transcription(lines, partner_speakers)
    _reset_cancel()

    # Шаг 1: саммаризация транскрипции в markdown-саммари (product-менеджер стиль)
    step1_system = (prompt_prefix or "").strip()
    if step1_system:
        step1_system = step1_system.rstrip() + "\n\n"
    step1_system += SUMMARY_PROMPT_TEMPLATE.strip()
    step1_user = "Текст транскрипции:\n\n" + annotated
    logger.info("Groq step1 (summary): model=%s len=%s", GROQ_MODEL, len(annotated))
    try:
        response1 = _grok_chat(step1_system, step1_user, format_json=False, cancel_event=_cancel_event, api_key=api_key)
    except Exception as e:
        return {"summary": "", "post_meeting_message": "", "tasks": [], "error": str(e)}
    if _cancel_event.is_set():
        return {"summary": "", "post_meeting_message": "", "tasks": [], "error": "Генерация отменена пользователем"}
    summary_markdown = (response1 or "").strip()
    if not summary_markdown:
        return {"summary": "", "post_meeting_message": "", "tasks": [], "error": "Модель вернула пустой саммари"}

    # Шаг 2: из саммари извлечь post_meeting_message и tasks (JSON)
    step2_user = summary_markdown
    logger.info("Groq step2 (extract): model=%s summary_len=%s", GROQ_MODEL, len(summary_markdown))
    try:
        response2 = _grok_chat(
            EXTRACT_FROM_SUMMARY_PROMPT.strip(), step2_user,
            format_json=True, cancel_event=_cancel_event, api_key=api_key,
        )
    except Exception as e:
        return {"summary": summary_markdown, "post_meeting_message": "", "tasks": [], "error": str(e)}
    if _cancel_event.is_set():
        return {"summary": summary_markdown, "post_meeting_message": "", "tasks": [], "error": "Генерация отменена пользователем"}
    data = _parse_transcription_json_response(response2)
    if data is None:
        snippet = (response2 or "")[:500]
        logger.warning("Step2 returned unparseable JSON: %s", snippet)
        return {
            "summary": summary_markdown,
            "post_meeting_message": "",
            "tasks": [],
            "error": f"Не удалось разобрать ответ модели как JSON (постмит и задачи). Начало: {snippet}",
        }

    def _str_or_join(v):
        """Модель иногда возвращает список вместо строки — объединяем через \\n."""
        if isinstance(v, list):
            return "\\n".join(str(i) for i in v if i)
        return (v or "").strip()

    # summary — результат шага 1 (markdown); постмит и задачи — из JSON шага 2
    summary = summary_markdown
    post_meeting_message = _str_or_join(data.get("post_meeting_message"))

    # Получаем список имён партнёров и всех участников для замены
    meta = parse_transcription_metadata(raw_text)
    all_names = meta.get("speakers", [])
    partner_set = {s.strip() for s in partner_speakers if s}

    def _remove_names(text: str, for_description: bool = False) -> str:
        """Заменяет или убирает личные имена из транскрипции (все падежные формы)."""
        if not text:
            return text
        for name in all_names:
            first = name.split()[0]
            if len(first) < 3:
                continue
            # Используем prefix (4+ chars) чтобы ловить падежные формы: Настя/Насти/Настей/Настею и т.п.
            prefix = re.escape(first[:max(4, len(first)-2)])
            is_partner = name in partner_set or any(name.startswith(p.split()[0]) for p in partner_set)
            if for_description:
                # В описаниях — убираем имя с окружающими пробелами
                text = re.sub(rf'\s*\b{prefix}[а-яёА-ЯЁa-zA-Z]*\b\s*', ' ', text, flags=re.IGNORECASE)
                text = re.sub(r'  +', ' ', text).strip()
            else:
                replacement = 'партнёр' if is_partner else 'мы'
                text = re.sub(rf'\b{prefix}[а-яёА-ЯЁa-zA-Z]*\b', replacement, text, flags=re.IGNORECASE)
        return text

    # Применяем очистку к задачам
    tasks_raw = data.get("tasks") or []
    if isinstance(tasks_raw, dict):
        tasks_raw = list(tasks_raw.values())
    tasks = []
    for t in tasks_raw:
        if isinstance(t, dict):
            # Чистим имена в title и description
            if "title" in t:
                t["title"] = _remove_names(str(t["title"]))
            if "description" in t:
                t["description"] = _remove_names(str(t["description"]), for_description=True)
            norm = _normalize_task(t)
            if norm:
                tasks.append(norm)

    # Словарные ошибки модели и шаблонные фразы-пустышки
    _MODEL_WORD_FIXES_RX = [
        (re.compile(r'\bпришить\b', re.IGNORECASE), 'отправить'),
        (re.compile(r'\bпришивать\b', re.IGNORECASE), 'отправлять'),
        (re.compile(r'\bпришив\b', re.IGNORECASE), 'отправив'),
        (re.compile(r'\bпришил\b', re.IGNORECASE), 'отправил'),
        # Искажения "security/секьюрити"
        (re.compile(r'\bсекурит\w*\b', re.IGNORECASE), 'безопасность'),
        (re.compile(r'\bсекьюрит\w*\b', re.IGNORECASE), 'безопасность'),
        # Артефакт замены имён: предлог + "мы" в неправильном падеже
        (re.compile(r'\bс мы\b', re.IGNORECASE), 'с нашей стороны'),
        (re.compile(r'\bдля мы\b', re.IGNORECASE), 'для нас'),
        (re.compile(r'\bу мы\b', re.IGNORECASE), 'у нас'),
        (re.compile(r'\bо мы\b', re.IGNORECASE), 'о нас'),
    (re.compile(r'\bот мы\b', re.IGNORECASE), 'от нас'),
    # Жаргон "пошарить" (поделиться/прислать)
    (re.compile(r'\bпошарит\b', re.IGNORECASE), 'поделится'),
    (re.compile(r'\bпошарить\b', re.IGNORECASE), 'поделиться'),
    (re.compile(r'\bпошарил\b', re.IGNORECASE), 'поделился'),
    # Жаргон "скинуть" (отправить)
    (re.compile(r'\bскинеть\b', re.IGNORECASE), 'отправить'),
    (re.compile(r'\bскинуть\b', re.IGNORECASE), 'отправить'),
    (re.compile(r'\bскинет\b', re.IGNORECASE), 'отправит'),
    (re.compile(r'\bскинул\b', re.IGNORECASE), 'отправил'),
    (re.compile(r'\bскиньте\b', re.IGNORECASE), 'отправьте'),
    # "подаст информацию" → "поделится информацией"
    (re.compile(r'\bподаст\s+информацию\b', re.IGNORECASE), 'поделится информацией'),
]
    # Шаблонные концовки-пустышки в описаниях
    _BOILERPLATE_RX = re.compile(
        r',?\s*'
        r'(?:как\s+(?:указано|обговорено|обсуждалось|обсуждено|отмечено|описано)\s+в\s+(?:обсуждении|брифе|встрече)|'
        r'согласно\s+(?:обсуждению|обсуждённому|обсуждённым|обговорённым|брифу|встрече|заданию|требованиям|темам)|'
        r'согласно\s+(?:обсуждению|брифу)|'
        r'в\s+соответствии\s+с\s+(?:обсуждением|брифом|требованиями|обсуждёнными\s+\w+)|'
        r'чтобы\s+соответствовать\s+обсуждённым\s+\w+|'
        r'включив\s+их\s+в\s+проект|'
        r'согласовать\s+с\s+(?:нашей\s+стороны|нашей\s+стороной|партнёром)|'
        r'с\s+согласия\s+(?:партнёра|партнера|на\s+сайте|\w+)|'
        r'мяч\s+на\s+(?:нашей|моей|вашей|его|её|их)?\s*стороне|'
        r'по\s+согласованию\s+с\s+(?:партнёром|\w+)|'
        r'как\s+указано)',
        re.IGNORECASE,
    )

    # Паттерн для дублирующихся слов: "воронки воронки", "X и X"
    _DOUBLE_WORD_RX = re.compile(
        r'\b([а-яёА-ЯЁ]{4,})\s+\1\b|\b([а-яёА-ЯЁ]{4,})\s+и\s+\2\b',
        re.IGNORECASE,
    )

    # Глаголы в 3-м лице ед.ч. в начале задачи → инфинитив (артефакт модели)
    # "Повторит" → "Повторить", "Уточнит" → "Уточнить", "Подготовит" → "Подготовить"
    _VERB_3P_TO_INF = re.compile(
        r'^([А-ЯЁ][а-яё]{3,}(?:ш|в|вл|вр|вт|вш|зн|зв|зр|зт|зж|зн|нв|нт|нш|рг|рж|рт|рш|ст|сн|сш|тр|тв|тк)?(?:ит|ет|ёт|ит))\b',
    )

    # Глаголы, где -ит → -еть (а не -ить): корни 1-го спряжения
    _VERB_STEM_TO_ETJ = re.compile(
        r'^(?:посмотр|смотр|вид|слыш|болит|горит|сид|лет|стои|терп|верт|зависит|шумит|грем|звен|вис|бурл|кипит)',
        re.IGNORECASE,
    )

    def _fix_3p_verb(s: str) -> str:
        """Конвертирует глагол 3-го лица ед.ч. в инфинитив в начале строки."""
        m = _VERB_3P_TO_INF.match(s)
        if not m:
            return s
        verb = m.group(1)
        # -ует → -овать
        if verb.endswith('ует'):
            return verb[:-3] + 'овать' + s[m.end():]
        # -ит: проверяем исключения (1 спряжение → -еть)
        if verb.endswith('ит'):
            stem = verb[:-2].lower()
            if _VERB_STEM_TO_ETJ.match(stem):
                return verb[:-2] + 'еть' + s[m.end():]
            return verb[:-2] + 'ить' + s[m.end():]
        # -ет/-ёт → -еть  (глаголы 1 спряжения)
        if verb.endswith(('ет', 'ёт')):
            return verb[:-2] + 'еть' + s[m.end():]
        return s

    def _fix_words(s: str) -> str:
        for rx, rep in _MODEL_WORD_FIXES_RX:
            s = rx.sub(rep, s)
        s = _BOILERPLATE_RX.sub('', s).strip().rstrip(',').strip()
        # Убираем задвоенные слова ("воронки воронки" → "воронки", "X и X" → "X")
        s = _DOUBLE_WORD_RX.sub(lambda m: m.group(1) or m.group(2), s)
        # Убираем ведущие союзы "и ", "а ", "но " в начале предложения
        s = re.sub(r'^(и|а|но)\s+', '', s, flags=re.IGNORECASE).strip()
        # Капитализируем первую букву
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
        # Убираем "он/она/они" как подлежащие в начале предложения — артефакт удаления имён
        s = re.sub(r'^(Он|Она|Они)\s+', '', s).strip()
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
        # Убираем оборванные концовки ", а [глагол]" или ", а прислать ..."
        s = re.sub(r',\s+а\s+\S+\s*$', '', s).strip()
        # Убираем "с/у/к/от ИмяФамилия" — люди, не попавшие в список спикеров
        # Паттерн ловит "с Аней Куляковой", "у Сергея Иванова" и пр. (только с заглавной буквы)
        s = re.sub(
            r'\s+(?:с|у|к|от|для)\s+[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{3,})?(?=\s*[,.\-—]|\s+(?:чтобы|для|и\s)|$)',
            '',
            s, flags=re.UNICODE,
        ).strip()
        # Если после чистки остался только предлог/союз в конце — убираем
        s = re.sub(r'\s+(?:с|у|к|от|для|и|а|но)\s*$', '', s, flags=re.IGNORECASE).strip()
        return s

    # Дополнительно фиксируем описания any-задач: если там "партнёр" — заменяем на "мы"
    for t in tasks:
        if t.get("assignee") == "any":
            d = t.get("description", "")
            t["description"] = re.sub(r'\bпартнёр(а|у|ом|е)?\b', 'мы', d, flags=re.IGNORECASE)

    # Дедупликация задач по похожести заголовков
    def _stem(w: str) -> str:
        for suffix in ('ание', 'ение', 'ацию', 'ировать', 'овать', 'ить', 'ать', 'ять',
                       'ний', 'ния', 'ий', 'ого', 'ому', 'ием', 'ию', 'ия',
                       'ей', 'ем', 'ях', 'ах', 'ами', 'ями', 'ям', 'ам', 'их'):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[: -len(suffix)]
        return w

    def _tasks_similar(a: str, b: str) -> bool:
        a_w = set(_stem(w) for w in re.findall(r'[а-яёa-z]{4,}', a.lower()))
        b_w = set(_stem(w) for w in re.findall(r'[а-яёa-z]{4,}', b.lower()))
        if not a_w or not b_w:
            return False
        # Если первое значимое слово (4+ букв) одинаковое — считаем похожими
        a_first = next((w for w in re.findall(r'[а-яёa-z]{4,}', a.lower())), '')
        b_first = next((w for w in re.findall(r'[а-яёa-z]{4,}', b.lower())), '')
        if a_first and b_first and _stem(a_first) == _stem(b_first):
            return True
        overlap = len(a_w & b_w) / min(len(a_w), len(b_w))
        return overlap >= 0.5

    # Шаблонные бессмысленные названия задач — фильтруем
    _GENERIC_TASK_TITLES = re.compile(
        r'^(?:уточнить детали(?: задачи)?|добавить информацию|добавить данные|уточнить информацию|'
        r'вернуть(ся)? к обсуждению|уточнить и вернуть|обсудить детали|разобрать детали|'
        r'проверить детали|уточнить детали и вернуть|добавление информации|добавление данных|'
        r'обсуждение \w+|обсудить \w+|обсудить и \w+)$',
        re.IGNORECASE,
    )

    deduped = []
    for t in tasks:
        title = t.get("title", "").strip()
        if _GENERIC_TASK_TITLES.match(title):
            continue  # выбрасываем пустышку
        if not any(_tasks_similar(title, d.get("title", "")) for d in deduped):
            deduped.append(t)
    tasks = deduped

    # Принудительно исправляем assignee на основе брифа — модель часто путает секции
    # Разбираем brief на any_items и partner_items
    brief_any_items: list[str] = []
    brief_partner_items: list[str] = []
    if brief:
        in_any = False
        in_partner = False
        for bline in brief.split('\n'):
            if 'ЗАДАЧИ НАШЕЙ СТОРОНЫ' in bline:
                in_any, in_partner = True, False
            elif 'ЗАДАЧИ ПАРТНЁРА' in bline:
                in_any, in_partner = False, True
            elif 'ТЕМЫ ВСТРЕЧИ' in bline:
                in_any, in_partner = False, False
            elif bline.strip().startswith('-') and in_any:
                brief_any_items.append(bline.strip().lstrip('-').strip())
            elif bline.strip().startswith('-') and in_partner:
                brief_partner_items.append(bline.strip().lstrip('-').strip())

    if brief_any_items or brief_partner_items:
        def _word_prefix(w: str, n: int = 5) -> str:
            return w[:n] if len(w) >= n else w

        def _best_brief_match(title: str, items: list[str]) -> float:
            if not items:
                return 0.0
            # Используем 5-символьный префикс для нечёткого сравнения
            title_pref = set(_word_prefix(w) for w in re.findall(r'[а-яёa-z]{5,}', title.lower()))
            best = 0.0
            for item in items:
                item_pref = set(_word_prefix(w) for w in re.findall(r'[а-яёa-z]{5,}', item.lower()))
                if not title_pref or not item_pref:
                    continue
                overlap = len(title_pref & item_pref) / min(len(title_pref), len(item_pref))
                if overlap > best:
                    best = overlap
            return best

        for t in tasks:
            title = t.get("title", "")
            score_any = _best_brief_match(title, brief_any_items)
            score_partner = _best_brief_match(title, brief_partner_items)
            # Если хоть немного лучше матчится к партнёру — ставим partner
            if score_partner > score_any + 0.1:
                t["assignee"] = "partner"
            elif score_any > score_partner + 0.1:
                t["assignee"] = "any"
            # Иначе оставляем как есть (модель выбрала)

    # В summary/post_meeting — просто убираем имена (не заменяем), чтобы не ломать падежи
    post_meeting_message = _remove_names(post_meeting_message, for_description=True)
    summary = _remove_names(summary, for_description=True)

    # Фильтруем "мусорные" строки в секции "Обсудили:" post_meeting_message
    _PM_JUNK_RX = re.compile(
        r'мяч\s+на\s+(?:нашей|моей|вашей|его|её|их)?\s*стороне|'
        r'\bмяч\b',
        re.IGNORECASE,
    )
    _PM_SECTION_JUNK_RX = re.compile(
        r'обсуждение\s+\w+|'
        r'обсуждение\s+рекомендац',
        re.IGNORECASE,
    )

    def _clean_pm_section(pm: str) -> str:
        """Убирает мусорные строки и перенумеровывает пункты в секции Обсудили."""
        lines = pm.split('\n')
        result = []
        in_discussed = False
        new_idx = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('Обсудили:'):
                in_discussed = True
                result.append(line)
                continue
            if stripped.startswith('Дальнейшие шаги:'):
                in_discussed = False
            if in_discussed:
                # Фильтруем мусор
                if _PM_JUNK_RX.search(stripped) or _PM_SECTION_JUNK_RX.search(stripped):
                    continue
                # Перенумеровываем
                numbered = re.match(r'^\d+\.\s+(.+)', stripped)
                if numbered:
                    new_idx += 1
                    result.append(f'{new_idx}. {numbered.group(1)}')
                    continue
            result.append(line)
        return '\n'.join(result)
    post_meeting_message = _clean_pm_section(post_meeting_message)

    # Исправляем словарные ошибки модели в задачах
    for t in tasks:
        t["title"] = _fix_3p_verb(_fix_words(t.get("title", "")))
        desc = _fix_words(t.get("description", ""))
        # Убираем "Партнёр/Партнер должен/нужно" в начале описания
        desc = re.sub(r'^партнёр[а-яё]?\s+(?:должен\s+|нужно\s+)?', '', desc, flags=re.IGNORECASE).strip()
        # Убираем "Мы должны/нужно" в начале описания any-задач
        if t.get("assignee") == "any":
            desc = re.sub(r'^мы\s+(?:должны\s+|нужно\s+)?', '', desc, flags=re.IGNORECASE).strip()
        if desc and desc[0].islower():
            desc = desc[0].upper() + desc[1:]
        # "Проведён обсуждение" → убираем такие причастные обороты
        desc = re.sub(r'^Проведён[оа]?\s+', '', desc).strip()
        # Имя в косвенном падеже в начале описания: "Аней Куляковой покажет..." → убираем имя
        desc = re.sub(
            r'^[А-ЯЁ][а-яё]{2,}(?:ей|ой|ею|ью|ым|им|ого|ему|ому)\s+(?:[А-ЯЁ][а-яё]{2,}\s+)?',
            '', desc,
        ).strip()
        if desc and desc[0].islower():
            desc = desc[0].upper() + desc[1:]
        # Применяем конвертацию 3л → инфинитив и в описаниях (для первого предложения)
        desc = _fix_3p_verb(desc)
        t["description"] = desc

    # Фиксируем any-задачи у которых title начинается с "Партнёр" (артефакт замены имён)
    # ВАЖНО: до генерации Дальнейших шагов!
    for t in tasks:
        if t.get("assignee") == "any":
            title = t.get("title", "")
            title = re.sub(r'^Партнёр\w*\s+', '', title).strip()
            if title and title[0].islower():
                title = title[0].upper() + title[1:]
            # После снятия префикса — снова конвертируем глагол в инфинитив
            title = _fix_3p_verb(title)
            t["title"] = title
            desc = t.get("description", "")
            t["description"] = re.sub(r'^Партнёр\w*\s+', '', desc).strip()

    # Перегенерируем "Дальнейшие шаги" из скорректированных задач
    # (модель часто путает наши/партнёрские)
    if tasks and post_meeting_message:
        any_actions = [t["title"] for t in tasks if t.get("assignee") == "any"]
        partner_actions = [t["title"] for t in tasks if t.get("assignee") == "partner"]
        if any_actions or partner_actions:
            steps_lines = []
            idx = 1
            for action in any_actions[:2]:
                steps_lines.append(f"{idx}. Мы — {action[0].lower() + action[1:]}")
                idx += 1
            for action in partner_actions[:2]:
                steps_lines.append(f"{idx}. С вашей стороны — {action[0].lower() + action[1:]}")
                idx += 1
            new_steps = "\n".join(steps_lines)
            # Заменяем секцию "Дальнейшие шаги:..." в post_meeting_message
            post_meeting_message = re.sub(
                r'Дальнейшие шаги:.*$',
                'Дальнейшие шаги:\n' + new_steps,
                post_meeting_message,
                flags=re.DOTALL,
            ).rstrip()

    # Убираем "партнёр должен X" из summary
    summary = re.sub(r'партнёр\s+должен\s+', '', summary, flags=re.IGNORECASE).strip()

    # Убираем "с/у/к Имя Фамилия" из summary и post_meeting (третьи лица)
    _INLINE_NAME_RX = re.compile(
        r'\s+(?:с|у|к|от|для|о\s+созвоне\s+с)\s+[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{3,})?'
        r'(?=\s*[,.\-—]|\s+(?:чтобы|для|и\b)|$)',
        re.UNICODE,
    )
    post_meeting_message = _INLINE_NAME_RX.sub('', post_meeting_message).strip()
    post_meeting_message = re.sub(r'  +', ' ', post_meeting_message)

    # Убираем "с/у/к Имя Фамилия" из summary (третьи лица, не попавшие в список спикеров)
    summary = re.sub(
        r'\s+(?:с|у|к|от|для|о\s+созвоне\s+с)\s+[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]{3,})?'
        r'(?=\s*[,.\-—]|\s+(?:чтобы|для|и\b)|$)',
        '',
        summary, flags=re.UNICODE,
    ).strip()
    # Убираем "приняли решение о созвоне" → слишком конкретная деталь без контекста
    summary = re.sub(r',?\s*приняли решение о созвоне\s*', ' ', summary, flags=re.IGNORECASE).strip()
    summary = re.sub(r'\s{2,}', ' ', summary)

    # Обрезаем summary если модель включила "Дальнейшие шаги" или "Далее" в конец
    summary = re.sub(r'\s*Дальнейшие шаги:.*$', '', summary, flags=re.DOTALL | re.IGNORECASE).strip()
    summary = re.sub(r'\s*Далее:.*$', '', summary, flags=re.DOTALL | re.IGNORECASE).strip()

    # Нормализуем summary: если нет нумерации — добавляем
    if summary and not re.search(r'^\s*1[\.\)]', summary):
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', summary) if len(s.strip()) > 10]
        if len(sentences) > 1:
            summary = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(sentences))

    return {"summary": summary, "post_meeting_message": post_meeting_message, "tasks": tasks}
