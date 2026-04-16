"""
Telegram уведомления для AM Hub.
Умные пуши: только важное, без спама.
"""
import os, logging, asyncio
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")


async def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Отправить сообщение через Telegram Bot API."""
    if not BOT_TOKEN or not chat_id:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as hx:
            r = await hx.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                      "disable_web_page_preview": True},
            )
            return r.status_code == 200
    except Exception as e:
        logger.warning(f"TG send error: {e}")
        return False


async def notify_overdue_checkup(chat_id: str, client_name: str, days: int, hub_url: str = ""):
    """⚠️ Просроченный чекап."""
    url_part = f'\n<a href="{hub_url}/clients">Открыть хаб →</a>' if hub_url else ""
    await send_message(chat_id, f"""⚠️ <b>Просроченный чекап</b>

Клиент: <b>{client_name}</b>
Дней без контакта: <b>{days}</b>{url_part}""")


async def notify_health_drop(chat_id: str, client_name: str, old_score: float,
                               new_score: float, hub_url: str = ""):
    """🔴 Падение Health Score."""
    drop = old_score - new_score
    emoji = "🔴" if new_score < 40 else "🟡"
    await send_message(chat_id, f"""{emoji} <b>Health Score упал</b>

Клиент: <b>{client_name}</b>
{old_score:.0f}% → <b>{new_score:.0f}%</b> (−{drop:.0f}%)""")


async def notify_task_overdue(chat_id: str, tasks: list, hub_url: str = ""):
    """📋 Просроченные задачи."""
    if not tasks:
        return
    lines = "\n".join(f"• {t['client_name']}: {t['title']}" for t in tasks[:5])
    extra = f"\n_...ещё {len(tasks)-5}_" if len(tasks) > 5 else ""
    url_part = f'\n<a href="{hub_url}/tasks">Открыть задачи →</a>' if hub_url else ""
    await send_message(chat_id, f"""📋 <b>Просроченные задачи ({len(tasks)})</b>

{lines}{extra}{url_part}""")


async def send_daily_digest(chat_id: str, stats: dict, hub_url: str = ""):
    """☀️ Утренний дайджест — только если есть что-то важное."""
    overdue   = stats.get("overdue_checkups", 0)
    tasks_due = stats.get("tasks_due_today",  0)
    health_crit = stats.get("health_critical", 0)
    meetings  = stats.get("meetings_today",   0)

    # Не шлём если всё хорошо
    if overdue == 0 and tasks_due == 0 and health_crit == 0 and meetings == 0:
        return

    lines = []
    if meetings:     lines.append(f"📅 Встреч сегодня: <b>{meetings}</b>")
    if tasks_due:    lines.append(f"✅ Задач к дедлайну: <b>{tasks_due}</b>")
    if overdue:      lines.append(f"⚠️ Просроченных чекапов: <b>{overdue}</b>")
    if health_crit:  lines.append(f"🔴 Критичных клиентов: <b>{health_crit}</b>")

    url_part = f'\n<a href="{hub_url}">Открыть хаб →</a>' if hub_url else ""
    await send_message(chat_id, f"""☀️ <b>Доброе утро!</b> Ваш день:

{chr(10).join(lines)}{url_part}""")


async def notify_sync_done(chat_id: str, clients: int, tasks: int):
    """✅ Sync завершён (только если много изменений)."""
    if clients + tasks < 10:
        return
    await send_message(chat_id, f"✅ Sync завершён: {clients} клиентов, {tasks} задач")
