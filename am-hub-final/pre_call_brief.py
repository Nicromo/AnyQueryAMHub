"""
Агрегатор данных для подготовки к встречам (Pre-Call Brief).
Собирает информацию из всех источников: Merchrules, Airtable, Time, Ktalk.
Генерирует умный бриф с рекомендациями через AI.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

import httpx

from merchrules_client import MerchrulesClient, get_auth_token_for_user
from time_integration import TimeClient
from ktalk_helper import KtalkClient, get_client_meetings_history
from airtable_sync import get_qbr_calendar_from_airtable

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


async def generate_pre_call_brief(
    client_name: str,
    site_id: str,
    manager_name: str,
    mr_login: str,
    mr_password: str,
    tg_id: int,
) -> Dict[str, Any]:
    """
    Генерирует полный бриф для подготовки к встрече с клиентом.
    
    Собирает данные из:
    - Merchrules (задачи, аналитика, роадмап, фиды)
    - Time (тикеты поддержки)
    - Ktalk (история встреч)
    - Airtable (QBR, последняя встреча)
    
    Возвращает структурированный бриф с рекомендациями.
    """
    brief = {
        "client_name": client_name,
        "site_id": site_id,
        "generated_at": datetime.now().isoformat(),
        "sections": {},
        "recommendations": [],
        "alerts": [],
        "quick_links": {},
    }
    
    async with httpx.AsyncClient(timeout=30) as hx:
        # 1. Авторизация в Merchrules
        mr_token = await get_auth_token_for_user(hx, tg_id, mr_login, mr_password)
        mr_client = MerchrulesClient()
        
        if mr_token:
            # 2. Получаем данные из Merchrules параллельно
            mr_tasks = []
            
            # Задачи
            mr_tasks.append(mr_client.get_content(hx, mr_token, limit=50))
            
            # Роадмап комментарии
            mr_tasks.append(mr_client.get_roadmap_comments(hx, mr_token, int(site_id), limit=5))
            
            # Следующая встреча
            mr_tasks.append(mr_client.get_next_meeting(hx, mr_token, int(site_id)))
            
            # Чекапы
            mr_tasks.append(mr_client.get_checkups(hx, mr_token, int(site_id)))
            
            # Фиды и статус
            mr_tasks.append(mr_client.get_feeds(hx, mr_token, int(site_id)))
            mr_tasks.append(mr_client.get_feed_status(hx, mr_token, int(site_id)))
            
            # Настройки поиска
            mr_tasks.append(mr_client.get_search_settings(hx, mr_token, int(site_id)))
            
            # Аналитика (за последнюю неделю)
            today = datetime.now()
            from_date = (today - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
            to_date = today.strftime("%Y-%m-%dT23:59:59")
            mr_tasks.append(mr_client.get_agg_report(
                hx, mr_token, int(site_id),
                ["ORDERS_TOTAL", "REVENUE_TOTAL", "CONVERSION", "RPS", "AOV"],
                from_date, to_date
            ))
            
            results = await asyncio.gather(*mr_tasks, return_exceptions=True)
            
            # Распределяем результаты
            brief["sections"]["merchrules"] = {
                "content": results[0] if not isinstance(results[0], Exception) else [],
                "roadmap_comments": results[1] if not isinstance(results[1], Exception) else [],
                "next_meeting": results[2] if not isinstance(results[2], Exception) else None,
                "checkups": results[3] if not isinstance(results[3], Exception) else [],
                "feeds": results[4] if not isinstance(results[4], Exception) else [],
                "feed_status": results[5] if not isinstance(results[5], Exception) else {},
                "search_settings": results[6] if not isinstance(results[6], Exception) else {},
                "analytics": results[7] if not isinstance(results[7], Exception) else {},
            }
            
            # Алерты по Merchrules
            feeds = brief["sections"]["merchrules"]["feeds"]
            feed_status = brief["sections"]["merchrules"]["feed_status"]
            
            if feed_status and feed_status.get("status") == "error":
                brief["alerts"].append({
                    "type": "critical",
                    "source": "Merchrules",
                    "message": f"Ошибка обработки фида: {feed_status.get('error', 'Неизвестная ошибка')}",
                })
            
            # Проверяем просроченные чекапы
            checkups = brief["sections"]["merchrules"]["checkups"]
            if checkups:
                for cu in checkups:
                    due_date = cu.get("due_date") or cu.get("dueDate")
                    if due_date and due_date < datetime.now().strftime("%Y-%m-%d"):
                        brief["alerts"].append({
                            "type": "warning",
                            "source": "Merchrules",
                            "message": f"Просрочен чекап от {due_date}",
                        })
        
        # 3. Получаем тикеты из Time
        try:
            support_summary = await get_account_support_summary(client_name, site_id)
            brief["sections"]["support"] = support_summary
            
            # Алерты по поддержке
            if support_summary.get("critical_tickets", 0) > 0:
                brief["alerts"].append({
                    "type": "critical",
                    "source": "Time",
                    "message": f"Есть {support_summary['critical_tickets']} критических тикетов!",
                })
            
            if support_summary.get("open_tickets", 0) > 5:
                brief["alerts"].append({
                    "type": "warning",
                    "source": "Time",
                    "message": f"Много открытых тикетов: {support_summary['open_tickets']}",
                })
        except Exception as exc:
            logger.warning("Failed to get support summary: %s", exc)
            brief["sections"]["support"] = {"error": str(exc)}
        
        # 4. Получаем историю встреч из Ktalk
        try:
            meetings_history = await get_client_meetings_history(client_name, site_id)
            brief["sections"]["meetings_history"] = meetings_history
            brief["quick_links"]["start_meeting"] = meetings_history.get("quick_link")
        except Exception as exc:
            logger.warning("Failed to get meetings history: %s", exc)
            brief["sections"]["meetings_history"] = {"error": str(exc)}
        
        # 5. Получаем QBR из Airtable
        try:
            qbr_events = await get_qbr_calendar_from_airtable()
            # Фильтруем по клиенту
            client_qbr = [
                e for e in qbr_events
                if client_name.lower() in str(e.get("fields", {})).lower()
            ]
            brief["sections"]["qbr"] = client_qbr[:3]  # Ближайшие 3
        except Exception as exc:
            logger.warning("Failed to get QBR calendar: %s", exc)
            brief["sections"]["qbr"] = {"error": str(exc)}
    
    # 6. Генерируем рекомендации через AI (если есть GROQ_API_KEY)
    if GROQ_API_KEY and len(brief["sections"]) > 1:
        try:
            ai_recommendations = await _generate_ai_recommendations(brief)
            brief["recommendations"] = ai_recommendations.get("recommendations", [])
            brief["talking_points"] = ai_recommendations.get("talking_points", [])
            brief["risks"] = ai_recommendations.get("risks", [])
        except Exception as exc:
            logger.warning("AI recommendations failed: %s", exc)
            brief["recommendations"] = ["Не удалось сгенерировать AI-рекомендации"]
    
    # 7. Формируем быстрые ссылки
    brief["quick_links"]["merchrules"] = f"https://merchrules.any-platform.ru/analytics/full?siteId={site_id}"
    brief["quick_links"]["time"] = f"https://time.tbank.ru/tinkoff/channels/any-team-support?q={client_name}"
    brief["quick_links"]["ktalk_recordings"] = f"https://tbank.ktalk.ru/content/artifacts?q={client_name}"
    
    return brief


async def _generate_ai_recommendations(brief: Dict) -> Dict[str, List[str]]:
    """
    Генерирует рекомендации через Groq API на основе собранных данных.
    """
    from groq import AsyncGroq
    
    client = AsyncGroq(api_key=GROQ_API_KEY)
    
    # Формируем контекст для AI
    context_parts = []
    
    # Поддержка
    support = brief["sections"].get("support", {})
    if support.get("open_tickets", 0) > 0:
        context_parts.append(
            f"Открытые тикеты: {support['open_tickets']}, "
            f"критические: {support.get('critical_tickets', 0)}"
        )
    
    # Merchrules
    mr = brief["sections"].get("merchrules", {})
    if mr.get("roadmap_comments"):
        comments = mr["roadmap_comments"][:3]
        context_parts.append(f"Последние комментарии из роадмапа: {comments}")
    
    if mr.get("feed_status") and mr["feed_status"].get("status") == "error":
        context_parts.append("⚠️ Есть ошибка в обработке фида!")
    
    # Встречи
    meetings = brief["sections"].get("meetings_history", {})
    if meetings.get("last_meeting_date"):
        context_parts.append(f"Последняя встреча была: {meetings['last_meeting_date']}")
    
    # QBR
    qbr = brief["sections"].get("qbr", [])
    if qbr:
        context_parts.append(f"Запланировано QBR: {len(qbr)} событий")
    
    prompt = f"""
Клиент: {brief['client_name']} (site_id: {brief['site_id']})

Контекст:
{chr(10).join('- ' + p for p in context_parts)}

Алерты:
{chr(10).join('- ' + a['message'] for a in brief.get('alerts', []))}

Задача:
1. Дай 3-5 ключевых рекомендаций для встречи с этим клиентом.
2. Предложи 3-5 тем для обсуждения (talking points).
3. Выдели основные риски.

Формат ответа JSON:
{{
  "recommendations": ["...", "..."],
  "talking_points": ["...", "..."],
  "risks": ["...", "..."]
}}
"""
    
    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        
        content = response.choices[0].message.content
        import json
        return json.loads(content)
    except Exception as exc:
        logger.warning("AI generation error: %s", exc)
        return {
            "recommendations": ["Проверьте статус тикетов поддержки", "Обсудите прогресс по роадмапу"],
            "talking_points": ["Текущие метрики", "Планы на квартал"],
            "risks": ["Возможные проблемы с фидами"],
        }


def format_brief_for_telegram(brief: Dict) -> str:
    """
    Форматирует бриф для отправки в Telegram.
    """
    lines = []
    lines.append(f"📋 <b>Бриф: {brief['client_name']}</b>")
    lines.append(f"Site ID: {brief['site_id']}")
    lines.append("")
    
    # Алерты
    if brief.get("alerts"):
        lines.append("⚠️ <b>Внимание!</b>")
        for alert in brief["alerts"]:
            emoji = "🔴" if alert["type"] == "critical" else "🟡"
            lines.append(f"{emoji} {alert['message']}")
        lines.append("")
    
    # Поддержка
    support = brief["sections"].get("support", {})
    if support.get("open_tickets") is not None:
        lines.append(f"🎫 <b>Поддержка:</b>")
        lines.append(f"  Открыто: {support['open_tickets']}")
        lines.append(f"  Критических: {support.get('critical_tickets', 0)}")
        lines.append("")
    
    # Merchrules
    mr = brief["sections"].get("merchrules", {})
    if mr.get("feeds"):
        lines.append(f"📊 <b>Фиды:</b> {len(mr['feeds'])}")
    if mr.get("feed_status") and mr["feed_status"].get("status"):
        status = mr["feed_status"]["status"]
        emoji = "🟢" if status == "ok" else "🔴"
        lines.append(f"  Статус: {emoji} {status}")
    lines.append("")
    
    # Рекомендации
    if brief.get("recommendations"):
        lines.append("💡 <b>Рекомендации:</b>")
        for i, rec in enumerate(brief["recommendations"][:5], 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")
    
    # Ссылки
    if brief.get("quick_links"):
        lines.append("🔗 <b>Быстрые ссылки:</b>")
        if "start_meeting" in brief["quick_links"]:
            lines.append(f"  📹 <a href=\"{brief['quick_links']['start_meeting']}\">Начать встречу</a>")
        if "merchrules" in brief["quick_links"]:
            lines.append(f"  📊 <a href=\"{brief['quick_links']['merchrules']}\">Аналитика</a>")
    
    return "\n".join(lines)


import asyncio
