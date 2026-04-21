"""
scheduler.py — APScheduler.
Все джобы читают креды из БД (user.settings), не только из env.
"""
import os
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

_scheduler = None


def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    return _scheduler


# ── Telegram helper ──────────────────────────────────────────────────────────

async def send_telegram(chat_id: int, text: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
        return resp.status_code == 200
    except Exception:
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_all_users_with_creds(db, cred_key: str):
    """Получить всех активных менеджеров у которых есть нужные креды."""
    from models import User
    users = db.query(User).filter(User.is_active == True).all()
    result = []
    for u in users:
        settings = u.settings or {}
        creds = settings.get(cred_key, {})
        if creds and any(creds.values()):
            result.append((u, creds))
    return result


# ── JOBS ─────────────────────────────────────────────────────────────────────

async def job_sync_merchrules():
    """Каждый час: синк Merchrules для ВСЕХ менеджеров с кредами в БД или env."""
    logger.info("🔄 Syncing Merchrules (all managers)...")
    try:
        from database import SessionLocal
        from models import Client, Task, Meeting, SyncLog, User
        from merchrules_sync import get_auth_token, fetch_site_tasks, fetch_site_meetings
        import httpx

        db = SessionLocal()
        base_url = os.environ.get("MERCHRULES_API_URL", "https://merchrules.any-platform.ru")

        # Собираем все источники кредов: env + каждый юзер из БД
        creds_list = []

        # 1. env-креды (глобальные)
        env_login = os.environ.get("MERCHRULES_LOGIN", "")
        env_pass = os.environ.get("MERCHRULES_PASSWORD", "")
        if env_login and env_pass:
            creds_list.append({"login": env_login, "password": env_pass,
                                "manager_email": None, "source": "env"})

        # 2. Персональные креды каждого менеджера
        users = db.query(User).filter(User.is_active == True).all()
        for u in users:
            settings = u.settings or {}
            mr = settings.get("merchrules", {})
            login = mr.get("login", "")
            from crypto import dec as _dec
            password = _dec(mr.get("password", "")) or ""
            if login and password and login != env_login:
                creds_list.append({"login": login, "password": password,
                                    "manager_email": u.email, "source": f"user:{u.email}"})

        if not creds_list:
            logger.info("Merchrules: no credentials found in env or user settings")
            db.close()
            return

        total_tasks = 0
        total_clients = 0

        for cred in creds_list:
            login = cred["login"]
            password = cred["password"]
            manager_email = cred["manager_email"]

            sync_log = SyncLog(
                integration="merchrules", resource_type="all",
                action="sync", status="in_progress",
                sync_data={"source": cred["source"]},
            )

            try:
                async with httpx.AsyncClient(timeout=30) as hx:
                    # Перебираем поля логина
                    token = None
                    for field in ("email", "login", "username"):
                        try:
                            r = await hx.post(
                                f"{base_url}/backend-v2/auth/login",
                                json={field: login, "password": password},
                                timeout=15,
                            )
                            if r.status_code == 200:
                                body = r.json()
                                token = body.get("token") or body.get("access_token") or body.get("accessToken")
                                if token:
                                    break
                        except Exception:
                            continue

                    if not token:
                        sync_log.status = "error"
                        sync_log.message = f"Auth failed for {login}"
                        logger.warning(f"MR auth failed: {login}")
                        db.add(sync_log)
                        db.commit()
                        continue

                    headers = {"Authorization": f"Bearer {token}"}

                    # Тянем аккаунты
                    accounts = []
                    for ep in (
                        f"{base_url}/backend-v2/accounts?limit=500",
                        f"{base_url}/backend-v2/sites?limit=500",
                    ):
                        try:
                            r = await hx.get(ep, headers=headers, timeout=20)
                            if r.status_code == 200:
                                data = r.json()
                                for key in ("accounts", "sites", "items", "data"):
                                    if isinstance(data.get(key), list) and data[key]:
                                        accounts = data[key]
                                        break
                                if not accounts and isinstance(data, list):
                                    accounts = data
                                if accounts:
                                    break
                        except Exception:
                            continue

                    for acc in accounts:
                        aid = acc.get("id") or acc.get("site_id") or acc.get("siteId")
                        if not aid:
                            continue
                        site_id = str(aid)
                        acc_name = acc.get("name") or acc.get("title") or acc.get("domain") or f"Account {site_id}"

                        c = db.query(Client).filter(Client.merchrules_account_id == site_id).first()
                        if not c:
                            c = Client(
                                merchrules_account_id=site_id,
                                name=acc_name,
                                manager_email=manager_email,
                                segment=acc.get("segment") or acc.get("tariff") or None,
                            )
                            db.add(c)
                            db.flush()
                            total_clients += 1
                        else:
                            c.name = acc_name
                            if manager_email and not c.manager_email:
                                c.manager_email = manager_email

                        # Tasks
                        try:
                            r_tasks = await hx.get(
                                f"{base_url}/backend-v2/tasks",
                                params={"site_id": site_id, "limit": 100},
                                headers=headers, timeout=15,
                            )
                            if r_tasks.status_code == 200:
                                td = r_tasks.json()
                                tasks_list = td.get("tasks") or td.get("items") or (td if isinstance(td, list) else [])
                                for t in tasks_list:
                                    tid = str(t.get("id", ""))
                                    if not tid:
                                        continue
                                    existing = db.query(Task).filter(Task.merchrules_task_id == tid).first()
                                    if not existing:
                                        db.add(Task(
                                            client_id=c.id, merchrules_task_id=tid,
                                            title=t.get("title") or t.get("name") or "",
                                            status=t.get("status", "plan"),
                                            priority=t.get("priority", "medium"),
                                            source="roadmap",
                                            team=t.get("team") or t.get("assignee") or None,
                                        ))
                                        total_tasks += 1
                                    else:
                                        existing.status = t.get("status", existing.status)
                        except Exception as e:
                            logger.debug(f"Tasks fetch failed for {site_id}: {e}")

                        # Meetings — last date
                        try:
                            r_m = await hx.get(
                                f"{base_url}/backend-v2/meetings",
                                params={"site_id": site_id, "limit": 10},
                                headers=headers, timeout=15,
                            )
                            if r_m.status_code == 200:
                                md = r_m.json()
                                ml = md.get("meetings") or md.get("items") or (md if isinstance(md, list) else [])
                                dates = [str(m.get("date") or m.get("meeting_date") or "")[:19]
                                         for m in ml if m.get("date") or m.get("meeting_date")]
                                if dates:
                                    try:
                                        c.last_meeting_date = datetime.fromisoformat(max(dates))
                                    except Exception:
                                        pass
                        except Exception as e:
                            logger.debug(f"Meetings fetch failed for {site_id}: {e}")

                db.commit()
                sync_log.status = "success"
                sync_log.records_processed = total_tasks
                logger.info(f"✅ MR sync done [{cred['source']}]: {len(accounts)} accounts, {total_tasks} tasks")

            except Exception as e:
                db.rollback()
                sync_log.status = "error"
                sync_log.message = str(e)
                logger.error(f"❌ MR sync error [{cred['source']}]: {e}")
                # Уведомление менеджеру через inbox+TG
                try:
                    from tg_notifications import notify_manager
                    mgr_email = cred.get("manager_email")
                    if mgr_email:
                        mgr = db.query(User).filter(User.email == mgr_email).first()
                        if mgr:
                            await notify_manager(db, mgr, "sync_fail",
                                {"integration": "Merchrules", "error": str(e)[:200]},
                                related_type="integration", related_id=None)
                except Exception as _ne:
                    logger.warning(f"notify_manager sync_fail skipped: {_ne}")

            db.add(sync_log)
            db.commit()

        db.close()
        logger.info(f"✅ Merchrules total: {total_clients} new clients, {total_tasks} new tasks")
    except Exception as e:
        logger.error(f"❌ job_sync_merchrules: {e}")


async def job_sync_airtable_clients():
    """Каждый час: синк клиентов из Airtable для КАЖДОГО менеджера со своим токеном."""
    logger.info("🔄 Syncing Airtable clients (all managers)...")
    try:
        from database import SessionLocal
        from models import Client, SyncLog, User
        from airtable_sync import sync_clients_from_airtable

        db = SessionLocal()

        # Собираем токены: env (глобальный) + персональные из user.settings
        token_sources = []
        global_token = os.environ.get("AIRTABLE_TOKEN") or os.environ.get("AIRTABLE_PAT", "")
        if global_token:
            token_sources.append({"token": global_token, "manager_email": None, "source": "env"})

        users = db.query(User).filter(User.is_active == True).all()
        for u in users:
            settings = u.settings or {}
            at = settings.get("airtable", {})
            t = at.get("pat") or at.get("token", "")
            if t and t != global_token:
                token_sources.append({"token": t, "manager_email": u.email, "source": f"user:{u.email}"})

        if not token_sources:
            logger.info("Airtable: no tokens found")
            db.close()
            return

        for src in token_sources:
            sync_log = SyncLog(integration="airtable", resource_type="clients",
                               action="sync", status="in_progress",
                               sync_data={"source": src["source"]})
            try:
                result = await sync_clients_from_airtable(
                    db=db,
                    token=src["token"],
                    default_manager_email=src["manager_email"],
                )
                sync_log.status = "success"
                sync_log.records_processed = result.get("synced", 0)
                logger.info(f"✅ Airtable [{src['source']}]: {result}")
            except Exception as e:
                db.rollback()
                sync_log.status = "error"
                sync_log.message = str(e)
                logger.error(f"❌ Airtable sync error [{src['source']}]: {e}")
            finally:
                db.add(sync_log)
                db.commit()

        db.close()
    except Exception as e:
        logger.error(f"❌ job_sync_airtable: {e}")


async def job_sync_ktalk_meetings():
    """Каждые 30 мин: синк встреч из Ktalk для всех менеджеров с токеном."""
    logger.info("🔄 Syncing Ktalk meetings...")
    try:
        from database import SessionLocal
        from models import Client, Meeting, Task, SyncLog, User
        from meeting_slots import create_slots_for_meeting
        from integrations.ktalk import get_events, get_transcript

        db = SessionLocal()
        sync_log = SyncLog(integration="ktalk", resource_type="meetings", action="sync", status="in_progress")

        total_new = 0
        total_slots = 0
        now = datetime.utcnow()
        window_from = now - timedelta(hours=2)
        window_to = now + timedelta(days=7)

        # Глобальный токен из env
        global_token = os.environ.get("KTALK_API_TOKEN", "")
        global_space = os.environ.get("KTALK_SPACE", "")

        token_sources = []
        if global_token and global_space:
            token_sources.append({"token": global_token, "space": global_space, "user": None})

        # Персональные токены менеджеров
        users = db.query(User).filter(User.is_active == True).all()
        for u in users:
            settings = u.settings or {}
            kt = settings.get("ktalk", {})
            t = kt.get("access_token", "")
            s = kt.get("space", "") or global_space
            if t and s and t != global_token:
                token_sources.append({"token": t, "space": s, "user": u})

        for src in token_sources:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=20) as hx:
                    base = f"https://{src['space']}.ktalk.ru"
                    headers = {"Content-Type": "application/json", "X-Auth-Token": src["token"]}
                    params = {
                        "dateFrom": window_from.isoformat(),
                        "dateTo": window_to.isoformat(),
                        "limit": 100,
                        "withCanceled": "false",
                    }
                    resp = await hx.get(
                        f"{base}/api/v1/spaces/{src['space']}/events",
                        headers=headers, params=params,
                    )
                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    events = data.get("events") or data.get("items") or []

                    for e in events:
                        ext_id = f"ktalk_{e.get('id', '')}"
                        if not e.get("id"):
                            continue
                        existing = db.query(Meeting).filter(Meeting.external_id == ext_id).first()

                        # Парсим дату
                        start_raw = e.get("start") or e.get("startDate", "")
                        start = None
                        try:
                            start = datetime.fromisoformat(str(start_raw).replace("Z", ""))
                        except Exception:
                            pass
                        if not start:
                            continue

                        # Тип встречи по названию
                        title_lower = (e.get("title") or e.get("name") or "").lower()
                        mtype = "meeting"
                        for kw, mt in [("qbr", "qbr"), ("чекап", "checkup"), ("checkup", "checkup"),
                                       ("онбординг", "onboarding"), ("kickoff", "kickoff"),
                                       ("апсейл", "upsell"), ("upsell", "upsell"), ("sync", "sync")]:
                            if kw in title_lower:
                                mtype = mt
                                break

                        # Ищем клиента
                        client = None
                        all_clients = db.query(Client).all()
                        participants = e.get("participants", [])
                        for c in all_clients:
                            if c.name.lower() in title_lower:
                                client = c
                                break
                            for p in participants:
                                pname = (p.get("name") or "").lower()
                                pemail = (p.get("email") or "").lower()
                                if c.name.lower() in pname or (c.domain and c.domain.lower() in pemail):
                                    client = c
                                    break
                            if client:
                                break

                        if existing:
                            # Обновляем запись
                            if client and not existing.client_id:
                                existing.client_id = client.id
                        else:
                            meeting = Meeting(
                                client_id=client.id if client else None,
                                date=start,
                                type=mtype,
                                title=e.get("title") or e.get("name") or "",
                                source="ktalk",
                                external_id=ext_id,
                                followup_status="pending",
                                attendees=[{"name": p.get("name", ""), "email": p.get("email", "")}
                                           for p in participants],
                            )
                            db.add(meeting)
                            db.flush()
                            total_new += 1

                            # Слоты prep/followup
                            if client:
                                slots = create_slots_for_meeting(db, meeting)
                                total_slots += len(slots)

                            # Подтягиваем транскрипт если встреча уже прошла
                            if start < now and e.get("recordingAvailable"):
                                try:
                                    tr_resp = await hx.get(
                                        f"{base}/api/v1/spaces/{src['space']}/events/{e['id']}/transcript",
                                        headers=headers,
                                    )
                                    if tr_resp.status_code == 200:
                                        tr_data = tr_resp.json()
                                        text = tr_data.get("text", "")
                                        if text and not existing:
                                            meeting.transcript = text[:5000]
                                except Exception:
                                    pass

                db.commit()
                logger.info(f"✅ Ktalk synced [{src['space']}]: {total_new} new meetings, {total_slots} slots")

            except Exception as e:
                db.rollback()
                logger.error(f"❌ Ktalk sync error [{src.get('space')}]: {e}")

        sync_log.status = "success"
        sync_log.records_processed = total_new
        db.add(sync_log)
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"❌ job_sync_ktalk: {e}")


async def job_deadline_reminders():
    """Каждый день 08:30: напоминания о задачах со сроком сегодня/завтра."""
    logger.info("⏰ Sending deadline reminders...")
    try:
        from database import SessionLocal
        from models import User, Task, Client
        db = SessionLocal()
        now_msk = datetime.now(MSK)
        today = now_msk.date()
        tomorrow = today + timedelta(days=1)

        # Уведомляем всех активных (TG — опционально, inbox — всем)
        users = db.query(User).filter(User.is_active == True).all()
        for user in users:
            # Задачи со сроком сегодня
            today_tasks = db.query(Task).join(
                Client, Task.client_id == Client.id, isouter=True
            ).filter(
                Task.due_date >= datetime.combine(today, datetime.min.time()),
                Task.due_date < datetime.combine(tomorrow, datetime.min.time()),
                Task.status.in_(["plan", "in_progress"]),
                Client.manager_email == user.email,
            ).all()

            # Задачи со сроком завтра
            tmr_tasks = db.query(Task).join(
                Client, Task.client_id == Client.id, isouter=True
            ).filter(
                Task.due_date >= datetime.combine(tomorrow, datetime.min.time()),
                Task.due_date < datetime.combine(tomorrow + timedelta(days=1), datetime.min.time()),
                Task.status.in_(["plan", "in_progress"]),
                Client.manager_email == user.email,
            ).all()

            # Inbox-уведомления по каждой задаче завтра (dedupe по task_id за 6 часов)
            try:
                from tg_notifications import notify_manager
                for t in tmr_tasks:
                    cn = t.client.name if t.client else "—"
                    await notify_manager(db, user, "task_deadline",
                        {"title": t.title, "client": cn,
                         "due": t.due_date.strftime("%d.%m %H:%M") if t.due_date else "—"},
                        related_type="task", related_id=t.id)
            except Exception as _ne:
                logger.warning(f"notify task_deadline skipped: {_ne}")

            # Просроченные
            overdue = db.query(Task).join(
                Client, Task.client_id == Client.id, isouter=True
            ).filter(
                Task.due_date < datetime.combine(today, datetime.min.time()),
                Task.status.in_(["plan", "in_progress", "blocked"]),
                Client.manager_email == user.email,
            ).all()

            if not today_tasks and not tmr_tasks and not overdue:
                continue

            msg = f"⏰ <b>Напоминания о задачах</b>\n"
            msg += f"<i>{now_msk.strftime('%d.%m.%Y')}</i>\n\n"

            if overdue:
                msg += f"<b>🔴 Просрочено ({len(overdue)}):</b>\n"
                for t in overdue[:5]:
                    client_name = t.client.name if t.client else "—"
                    msg += f"• {t.title} <i>[{client_name}]</i>\n"
                if len(overdue) > 5:
                    msg += f"  <i>…ещё {len(overdue)-5}</i>\n"
                msg += "\n"

            if today_tasks:
                msg += f"<b>📋 Сегодня ({len(today_tasks)}):</b>\n"
                for t in today_tasks[:5]:
                    client_name = t.client.name if t.client else "—"
                    msg += f"• {t.title} <i>[{client_name}]</i>\n"
                msg += "\n"

            if tmr_tasks:
                msg += f"<b>📅 Завтра ({len(tmr_tasks)}):</b>\n"
                for t in tmr_tasks[:3]:
                    client_name = t.client.name if t.client else "—"
                    msg += f"• {t.title} <i>[{client_name}]</i>\n"

            if user.telegram_id:
                await send_telegram(int(user.telegram_id), msg)

        db.commit()
        db.close()
        logger.info(f"✅ Deadline reminders sent to {len(users)} users")
    except Exception as e:
        logger.error(f"❌ job_deadline_reminders: {e}")


async def job_check_overdue_checkups():
    """Ежедневно 08:00: создать задачи на просроченные чекапы."""
    from models import CHECKUP_INTERVALS
    logger.info("🔔 Checking overdue checkups...")
    try:
        from database import SessionLocal
        from models import Client, Task, SyncLog
        db = SessionLocal()
        sync_log = SyncLog(integration="system", resource_type="checkups", action="check", status="in_progress")
        try:
            clients = db.query(Client).all()
            created = 0
            for c in clients:
                interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
                last = c.last_meeting_date or c.last_checkup
                if last and (datetime.utcnow() - last).days > interval:
                    existing = db.query(Task).filter(
                        Task.client_id == c.id, Task.source == "checkup",
                        Task.status.in_(["plan", "in_progress"])
                    ).first()
                    if not existing:
                        db.add(Task(
                            client_id=c.id,
                            title=f"Чекап: {c.name}",
                            description=f"Последний контакт {(datetime.utcnow()-last).days} дн. назад (интервал {interval} дн.)",
                            status="plan", priority="high", source="checkup",
                        ))
                        c.needs_checkup = True
                        created += 1
            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = created
            logger.info(f"✅ Created {created} checkup tasks")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ job_check_overdue_checkups: {e}")


async def job_morning_plan():
    """Пн-Пт 09:00 МСК: утренний план + встречи дня."""
    logger.info("📋 Morning plan...")
    try:
        from database import SessionLocal
        from models import Client, Task, Meeting, User, CheckUp
        db = SessionLocal()
        now_msk = datetime.now(MSK)
        today = now_msk.date()
        tomorrow = today + timedelta(days=1)

        meetings_today = db.query(Meeting).filter(
            Meeting.date >= datetime.combine(today, datetime.min.time()),
            Meeting.date < datetime.combine(tomorrow, datetime.min.time()),
        ).all()

        overdue = db.query(CheckUp).filter(CheckUp.status == "overdue").all()

        today_tasks = db.query(Task).filter(
            Task.due_date >= datetime.combine(today, datetime.min.time()),
            Task.due_date < datetime.combine(tomorrow, datetime.min.time()),
            Task.status.in_(["plan", "in_progress"]),
        ).all()

        users = db.query(User).filter(User.telegram_id != None, User.is_active == True).all()
        for user in users:
            msg = f"☀️ <b>Доброе утро, {user.first_name or user.email}!</b>\n"
            msg += f"📅 {now_msk.strftime('%d.%m.%Y')}\n\n"

            user_meetings = meetings_today
            if user.role == "manager":
                user_meetings = [m for m in meetings_today if m.client and m.client.manager_email == user.email]

            if user_meetings:
                msg += f"<b>📅 Встречи ({len(user_meetings)}):</b>\n"
                for m in user_meetings:
                    time_str = m.date.strftime("%H:%M") if m.date else "—"
                    client_name = m.client.name if m.client else "—"
                    msg += f"• <b>{time_str}</b> — {client_name}: {m.title or m.type}\n"
                msg += "\n"

            user_tasks = today_tasks
            if user.role == "manager":
                user_tasks = [t for t in today_tasks if t.client and t.client.manager_email == user.email]
            if user_tasks:
                msg += f"<b>📋 Задачи на сегодня ({len(user_tasks)}):</b>\n"
                for t in user_tasks[:5]:
                    msg += f"• {t.title}\n"
                if len(user_tasks) > 5:
                    msg += f"  <i>…ещё {len(user_tasks)-5}</i>\n"
                msg += "\n"

            if overdue:
                msg += f"<b>🔴 Просроченных чекапов: {len(overdue)}</b>\n"

            app_url = os.environ.get("APP_URL", "/today")
            msg += f"\n<a href=\"{app_url}\">📱 Открыть AM Hub</a>"

            await send_telegram(int(user.telegram_id), msg)

        logger.info(f"✅ Morning plan sent to {len(users)} users")
        db.close()
    except Exception as e:
        logger.error(f"❌ job_morning_plan: {e}")


async def job_weekly_digest():
    """Пятница 17:00: еженедельный дайджест."""
    logger.info("📊 Weekly digest...")
    try:
        from database import SessionLocal
        from models import Client, Task, Meeting, User
        db = SessionLocal()
        week_ago = datetime.utcnow() - timedelta(days=7)

        total_tasks = db.query(Task).count()
        done_tasks = db.query(Task).filter(Task.status == "done").count()
        week_tasks = db.query(Task).filter(Task.created_at >= week_ago).count()
        week_done = db.query(Task).filter(
            Task.confirmed_at >= week_ago, Task.status == "done"
        ).count()
        week_meetings = db.query(Meeting).filter(Meeting.date >= week_ago).count()
        clients = db.query(Client).count()

        users = db.query(User).filter(User.telegram_id != None, User.is_active == True).all()
        for user in users:
            # Персональная статистика
            my_done = db.query(Task).join(
                Client, Task.client_id == Client.id, isouter=True
            ).filter(
                Client.manager_email == user.email,
                Task.confirmed_at >= week_ago,
                Task.status == "done",
            ).count()

            my_meetings = db.query(Meeting).join(
                Client, Meeting.client_id == Client.id, isouter=True
            ).filter(
                Client.manager_email == user.email,
                Meeting.date >= week_ago,
            ).count()

            msg = f"📊 <b>Итоги недели</b>\n\n"
            msg += f"<b>Ваши результаты:</b>\n"
            msg += f"✅ Задач закрыто: {my_done}\n"
            msg += f"📅 Встреч проведено: {my_meetings}\n\n"
            msg += f"<b>По команде:</b>\n"
            msg += f"👥 Клиентов: {clients}\n"
            msg += f"📋 Задач за неделю: {week_tasks} (закрыто: {week_done})\n"
            msg += f"📅 Встреч: {week_meetings}\n\n"
            msg += f"Хороших выходных! 🎉"
            await send_telegram(int(user.telegram_id), msg)

        logger.info(f"✅ Weekly digest sent to {len(users)} users")
        db.close()
    except Exception as e:
        logger.error(f"❌ job_weekly_digest: {e}")


async def job_sync_meetings_and_slots():
    """Каждые 30 мин: синк встреч из Ktalk + Outlook, создание слотов."""
    # Делегируем в job_sync_ktalk_meetings (там же создаются слоты)
    await job_sync_ktalk_meetings()

    # Outlook
    outlook_client_id = os.environ.get("OUTLOOK_CLIENT_ID", "")
    if not outlook_client_id:
        return
    try:
        from database import SessionLocal
        from models import Client, Meeting, SyncLog
        from integrations.outlook import get_calendar_events
        from meeting_slots import create_slots_for_meeting

        db = SessionLocal()
        now = datetime.utcnow()
        events = await get_calendar_events(
            date_from=now - timedelta(hours=1),
            date_to=now + timedelta(days=7),
        )
        new_count = 0
        for e in events:
            ext_id = f"outlook_{e['external_id']}"
            if db.query(Meeting).filter(Meeting.external_id == ext_id).first():
                continue
            if not e.get("start"):
                continue

            client = None
            all_clients = db.query(Client).all()
            attendee_emails = [a["email"].lower() for a in e.get("attendees", [])]
            attendee_names = [a["name"].lower() for a in e.get("attendees", [])]
            for c in all_clients:
                c_lower = c.name.lower()
                if any(c_lower in n for n in attendee_names):
                    client = c
                    break
                if c.domain and any(c.domain.lower() in em for em in attendee_emails):
                    client = c
                    break

            meeting = Meeting(
                client_id=client.id if client else None,
                date=e["start"], type=e.get("meeting_type", "meeting"),
                title=e.get("title", ""), source="outlook",
                external_id=ext_id, followup_status="pending",
                attendees=[{"name": a["name"], "email": a["email"]}
                           for a in e.get("attendees", [])],
            )
            db.add(meeting)
            db.flush()
            if client:
                create_slots_for_meeting(db, meeting)
            new_count += 1

        db.commit()
        db.close()
        if new_count:
            logger.info(f"✅ Outlook: {new_count} new meetings synced")
    except Exception as e:
        logger.error(f"❌ job_sync_outlook: {e}")


# ── START ────────────────────────────────────────────────────────────────────



async def job_health_recalc_all():
    """Ночной пересчёт health score всех клиентов + TG алерт при падении."""
    logger.info("🔄 Recalculating health scores for all clients...")
    try:
        from database import SessionLocal
        from models import Client, User, HealthSnapshot, TelegramSubscription
        from routers.account_dashboard import _calculate_health
        db = SessionLocal()
        try:
            clients = db.query(Client).all()
            updated = 0
            dropped = []  # клиенты у которых упал health

            for c in clients:
                try:
                    old_score = c.health_score or 0
                    result = _calculate_health(c, db)
                    new_score = result["score"]
                    c.health_score = new_score
                    snap = HealthSnapshot(
                        client_id=c.id,
                        score=new_score,
                        components=result["components"],
                    )
                    db.add(snap)
                    updated += 1
                    # Фиксируем падение > 15%
                    if (old_score - new_score) > 0.15 and new_score < 0.6:
                        dropped.append((c, old_score, new_score))
                except Exception as e:
                    logger.warning(f"Health recalc failed for client {c.id}: {e}")

            db.commit()
            logger.info(f"✅ Health recalculated: {updated} clients, {len(dropped)} dropped")

            # TG алерт при падении health
            if dropped:
                subs = db.query(TelegramSubscription).filter(
                    TelegramSubscription.is_active == True,
                    TelegramSubscription.notify_health_drop == True,
                ).all()
                for sub in subs:
                    user = sub.user
                    if not user or not user.is_active:
                        continue
                    user_dropped = [
                        (c, old, new) for c, old, new in dropped
                        if user.role == "admin" or c.manager_email == user.email
                    ]
                    if not user_dropped:
                        continue
                    lines = [f"📉 <b>Падение Health Score ({len(user_dropped)} клиентов)</b>\n"]
                    for c, old, new in user_dropped[:8]:
                        icon = "🔴" if new < 0.3 else "🟡"
                        lines.append(
                            f"{icon} <b>{c.name}</b>: "
                            f"{round(old*100)}% → {round(new*100)}%"
                        )
                    await send_telegram(int(sub.chat_id), "\n".join(lines))
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Health recalc job error: {e}")


async def job_ktalk_sync():
    """Каждые 2 часа: синхронизация встреч из Контур.Толк."""
    ktalk_token = os.environ.get("KTALK_API_TOKEN", "")
    ktalk_space = os.environ.get("KTALK_SPACE", "")
    if not ktalk_token or not ktalk_space:
        return
    logger.info("🔄 Syncing Ktalk meetings...")
    try:
        from database import SessionLocal
        from models import Client, Meeting, SyncLog
        import httpx
        db = SessionLocal()
        sync_log = SyncLog(
            integration="ktalk", resource_type="meetings",
            action="sync", status="in_progress"
        )
        db.add(sync_log)
        db.commit()
        synced = 0
        try:
            base_url = os.environ.get("KTALK_BASE_URL", "https://tbank.ktalk.ru")
            headers = {
                "Authorization": f"Bearer {ktalk_token}",
                "Content-Type": "application/json",
            }
            from datetime import timezone
            since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            async with httpx.AsyncClient(timeout=30) as hx:
                resp = await hx.get(
                    f"{base_url}/api/v1/events",
                    headers=headers,
                    params={"from": since, "limit": 100},
                )
                if resp.status_code != 200:
                    raise Exception(f"Ktalk API {resp.status_code}: {resp.text[:200]}")
                events = resp.json().get("events", resp.json().get("data", []))

            clients = db.query(Client).all()
            client_map = {c.name.lower(): c for c in clients}

            for event in events:
                ext_id = str(event.get("id", ""))
                if not ext_id:
                    continue
                # Ищем уже существующую встречу
                existing = db.query(Meeting).filter(Meeting.external_id == ext_id).first()
                if existing:
                    # Обновляем транскрипцию/запись если появились
                    if event.get("recording_url") and not existing.recording_url:
                        existing.recording_url = event["recording_url"]
                    if event.get("transcript_url") and not existing.transcript_url:
                        existing.transcript_url = event["transcript_url"]
                    continue

                # Новая встреча — ищем клиента по участникам
                title = event.get("title", "")
                attendees = event.get("attendees", event.get("participants", []))
                client_obj = None
                for att in attendees:
                    email_or_name = att.get("email", att.get("name", "")).lower()
                    for cname, c in client_map.items():
                        if cname in email_or_name or email_or_name in cname:
                            client_obj = c
                            break
                    if client_obj:
                        break

                if not client_obj:
                    continue  # не нашли клиента — пропускаем

                try:
                    from datetime import datetime as dt
                    meet_date = dt.fromisoformat(
                        event.get("started_at", event.get("date", "")).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    continue

                meet = Meeting(
                    client_id=client_obj.id,
                    external_id=ext_id,
                    title=title,
                    date=meet_date,
                    type="sync",
                    source="ktalk",
                    recording_url=event.get("recording_url"),
                    transcript_url=event.get("transcript_url"),
                    attendees=[a.get("email", a.get("name", "")) for a in attendees],
                    followup_status="pending",
                )
                db.add(meet)
                client_obj.last_meeting_date = meet_date
                synced += 1

            db.commit()
            sync_log.status = "success"
            sync_log.records_processed = synced
            logger.info(f"✅ Ktalk synced: {synced} new meetings")
        except Exception as e:
            db.rollback()
            sync_log.status = "error"
            sync_log.message = str(e)
            logger.error(f"❌ Ktalk sync error: {e}")
        finally:
            db.add(sync_log)
            db.commit()
            db.close()
    except Exception as e:
        logger.error(f"❌ Ktalk job error: {e}")


async def job_renewal_alerts():
    """Еженедельно ПН 09:30: алерты о клиентах с высоким риском оттока."""
    logger.info("📊 Checking renewal risks...")
    try:
        from database import SessionLocal
        from models import Client, Task, User, TelegramSubscription
        db = SessionLocal()
        try:
            subs = db.query(TelegramSubscription).filter(
                TelegramSubscription.is_active == True,
            ).all()
            now = datetime.now()
            for sub in subs:
                user = sub.user
                if not user or not user.is_active:
                    continue
                q = db.query(Client)
                if user.role == "manager":
                    q = q.filter(Client.manager_email == user.email)
                clients = q.all()
                at_risk = []
                for c in clients:
                    health = round((c.health_score or 0) * 100)
                    days_silent = (now - c.last_meeting_date).days if c.last_meeting_date else 999
                    tasks = db.query(Task).filter(Task.client_id == c.id).all()
                    blocked = sum(1 for t in tasks if t.status == "blocked")
                    risk = (1-(c.health_score or 0))*30 + min(25, days_silent/90*25) + min(15, blocked*5)
                    risk = min(100, round(risk))
                    if risk >= 50:
                        at_risk.append((risk, c.name, c.segment or "—", c.mrr or 0))
                if not at_risk:
                    continue
                at_risk.sort(reverse=True)
                lines = [f"📊 <b>Риски продления на неделю ({len(at_risk)} клиентов)</b>\n"]
                for risk, name, seg, mrr in at_risk[:10]:
                    mrr_s = f"{mrr/1e3:.0f}K" if mrr >= 1000 else str(int(mrr))
                    icon = "🔴" if risk >= 70 else "🟡"
                    lines.append(f"{icon} {name} [{seg}] — риск {risk}%, MRR {mrr_s}₽")
                await send_telegram(int(sub.chat_id), "\n".join(lines))
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Renewal alerts error: {e}")

# ── Auto-task rules with actions engine (ported from main) ──────────────────

def job_auto_task_rules():
    """Плановые триггеры (health_drop, days_no_contact, checkup_due,
    payment_overdue, nps_low). Событийные триггеры (meeting_done,
    followup_sent, task_done) выполняются через auto_actions.fire_event
    из роутеров."""
    from database import SessionLocal
    from models import AutoTaskRule, Client
    try:
        from auto_actions import execute_actions
    except Exception as e:
        logger.warning(f"auto_actions not available: {e}")
        return
    planned = {"health_drop", "days_no_contact", "checkup_due",
               "payment_overdue", "nps_low", "task_blocked_days"}
    with SessionLocal() as db:
        rules = db.query(AutoTaskRule).filter(AutoTaskRule.is_active == True).all()
        now = datetime.utcnow()
        for r in rules:
            if r.trigger not in planned:
                continue
            q = db.query(Client)
            segs = r.segment_filter or []
            if segs:
                q = q.filter(Client.segment.in_(segs))
            cfg = r.trigger_config or {}
            for c in q.all():
                match = False
                if r.trigger == "health_drop":
                    match = (c.health_score or 0) < cfg.get("threshold", 0.5)
                elif r.trigger == "days_no_contact":
                    last = c.last_meeting_date or c.last_checkup
                    match = not last or (now - last).days >= cfg.get("days", 30)
                elif r.trigger == "checkup_due":
                    match = bool(c.needs_checkup)
                elif r.trigger == "payment_overdue":
                    match = getattr(c, "payment_status", None) == "overdue"
                elif r.trigger == "nps_low":
                    match = c.nps_last is not None and c.nps_last <= cfg.get("threshold", 6)
                if match:
                    try:
                        execute_actions(db, r, c)
                    except Exception:
                        logger.exception("auto rule %s client %s failed", r.id, c.id)


def job_sync_tbank_tickets():
    """Каждые 15 минут: тянет тикеты из Tbank Time (Mattermost) для всех пользователей с токеном."""
    from database import SessionLocal
    from models import User
    try:
        from integrations.tbank_time import ingest_tickets
    except Exception as e:
        logger.warning(f"tbank_time ingest not available: {e}")
        return
    import asyncio
    with SessionLocal() as db:
        users = db.query(User).filter(User.is_active == True).all()
        loop = asyncio.new_event_loop()
        try:
            for u in users:
                s = u.settings or {}
                tm = s.get("tbank_time", {}) or {}
                if not (tm.get("mmauthtoken") or tm.get("session_cookie")):
                    continue
                try:
                    loop.run_until_complete(ingest_tickets(db, u, limit_new=200, fetch_threads=True))
                except Exception:
                    logger.exception("tbank tickets sync user=%s failed", u.id)
        finally:
            loop.close()


def job_qbr_auto_collect():
    """Квартальный автосбор QBR по всем активным клиентам из Merchrules."""
    from database import SessionLocal
    from models import Client
    try:
        from qbr_auto_collect import collect_and_save, current_quarter
    except Exception as e:
        logger.warning(f"qbr_auto_collect not available: {e}")
        return
    import asyncio
    with SessionLocal() as db:
        q = current_quarter()
        clients = db.query(Client).all()
        loop = asyncio.new_event_loop()
        try:
            for c in clients:
                try:
                    loop.run_until_complete(collect_and_save(db, c, quarter=q, overwrite_text=False))
                except Exception:
                    logger.exception("qbr auto-collect client=%s failed", c.id)
        finally:
            loop.close()


def evaluate_auto_rules():
    """Раз в час проверяем все активные правила и создаём задачи по триггерам.
    Дедупликация: не создаём дубль Task с тем же title+client_id в последние 24ч."""
    from database import SessionLocal
    from models import AutoTaskRule, Task, Client, Meeting, User

    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)

    with SessionLocal() as db:
        rules = db.query(AutoTaskRule).filter(AutoTaskRule.is_active == True).all()
        total_created = 0

        for rule in rules:
            cfg = rule.trigger_config or {}
            # Клиенты в скоупе правила
            cq = db.query(Client)
            if rule.user_id:
                u = db.query(User).filter(User.id == rule.user_id).first()
                if u and u.email:
                    cq = cq.filter(Client.manager_email == u.email)
            seg_filter = rule.segment_filter or []
            if seg_filter:
                cq = cq.filter(Client.segment.in_(seg_filter))
            clients = cq.all()

            for c in clients:
                should = False
                if rule.trigger == "health_drop":
                    thr = float(cfg.get("threshold", 50))
                    # health_score хранится как доля (0..1) либо как процент (0..100)
                    hs = c.health_score
                    if hs is not None:
                        hs_pct = hs * 100 if hs <= 1 else hs
                        if hs_pct < thr:
                            should = True
                elif rule.trigger == "days_no_contact":
                    days = int(cfg.get("days", 30))
                    last = c.last_meeting_date or c.last_checkup
                    if last and (now - last).days >= days:
                        should = True
                elif rule.trigger == "meeting_done":
                    types = cfg.get("meeting_types") or ["checkup", "qbr"]
                    recent = db.query(Meeting).filter(
                        Meeting.client_id == c.id,
                        Meeting.type.in_(types),
                        Meeting.date >= day_ago,
                        Meeting.date <= now,
                    ).first()
                    if recent:
                        should = True
                elif rule.trigger == "checkup_due":
                    # скип — checkup_due проверяется отдельным джобом
                    pass

                if not should:
                    continue

                # Дедупликация: не создаём если такая же задача уже есть за сутки
                dup = db.query(Task).filter(
                    Task.client_id == c.id,
                    Task.title == rule.task_title,
                    Task.created_at >= day_ago,
                ).first()
                if dup:
                    continue

                due = now + timedelta(days=int(rule.task_due_days or 3))
                t = Task(
                    client_id=c.id,
                    title=rule.task_title,
                    description=rule.task_description or f"Авто по правилу: {rule.name}",
                    status="plan",
                    priority=rule.task_priority or "medium",
                    due_date=due,
                    source="automation",
                    task_type=rule.task_type or "followup",
                )
                db.add(t)
                total_created += 1

        if total_created:
            db.commit()
            logger.info(f"✅ evaluate_auto_rules: создано {total_created} задач")
        else:
            logger.info("evaluate_auto_rules: новых задач нет")


# ── Autotask jobs (dedupe через scheduler_utils.get_or_create_autotask) ──────
# Чекап-интервалы из models.CHECKUP_INTERVALS; триггерим за 5 дней до срока.
_CHECKUP_DUE_LEAD_DAYS = 5
_CHECKUP_DUE_RECENT_GUARD_DAYS = 30  # dedupe: не чаще 1 раза в 30 дней
_QBR_TRIGGER_OFFSET_DAYS = 83  # 90−7 — за неделю до квартала


async def job_meeting_reminder_30min():
    """Каждые 15 мин: если до встречи 25..40 минут — уведомить менеджера (inbox + TG)."""
    try:
        from database import SessionLocal
        from models import Client, Meeting, User
        from tg_notifications import notify_manager

        with SessionLocal() as db:
            now = datetime.utcnow()
            hi = now + timedelta(minutes=40)
            lo = now + timedelta(minutes=25)
            meetings = (db.query(Meeting)
                          .filter(Meeting.date >= lo, Meeting.date <= hi)
                          .all())
            for m in meetings:
                if not m.client_id:
                    continue
                client = db.query(Client).filter(Client.id == m.client_id).first()
                if not client or not client.manager_email:
                    continue
                u = db.query(User).filter(User.email == client.manager_email,
                                           User.is_active == True).first()
                if not u:
                    continue
                await notify_manager(db, u, "meeting_soon", {
                    "client": client.name,
                    "time": m.date.strftime("%H:%M"),
                    "type": m.type or "встреча",
                    "prep_url": f"/design/client/{client.id}",
                }, related_type="meeting", related_id=m.id)
            db.commit()
    except Exception as e:
        logger.error(f"❌ job_meeting_reminder_30min: {e}")


async def job_daily_meeting_prep():
    """Ежедневно 10:00 МСК: задача «Подготовиться к встрече» на каждую встречу сегодня."""
    logger.info("📋 daily_meeting_prep…")
    try:
        from database import SessionLocal
        from models import Client, Meeting
        from scheduler_utils import get_or_create_autotask

        with SessionLocal() as db:
            today_msk = datetime.now(MSK).date()
            day_start = datetime.combine(today_msk, datetime.min.time())
            day_end = day_start + timedelta(days=1)

            meetings = db.query(Meeting).filter(
                Meeting.date >= day_start,
                Meeting.date < day_end,
            ).all()

            created = 0
            for m in meetings:
                if not m.date or not m.client_id:
                    continue
                client = db.query(Client).filter(Client.id == m.client_id).first()
                if not client:
                    continue
                hhmm = m.date.strftime("%H:%M")
                title = f"Подготовиться к встрече с {client.name} — сегодня в {hhmm}"
                before = db.query(type(m).__bases__[0]).count() if False else None  # noqa
                t = get_or_create_autotask(
                    db,
                    client_id=client.id,
                    rule_key=f"meeting_prep:{m.id}",
                    target_date=today_msk,
                    manager_email=client.manager_email,
                    title=title,
                    task_type="meeting_prep",
                    due_date=m.date,
                    meta={"meeting_id": m.id},
                    priority="high",
                )
                if t and t.created_at and t.created_at >= datetime.utcnow() - timedelta(minutes=5):
                    created += 1
            db.commit()
            logger.info(f"✅ daily_meeting_prep: processed {len(meetings)} meetings, ~{created} new tasks")
    except Exception as e:
        logger.error(f"❌ job_daily_meeting_prep: {e}")


async def job_hourly_meeting_followup():
    """Каждый час: задача «Отправить фолоуап» для встреч, закончившихся в последние 2 часа."""
    logger.info("✍️  hourly_meeting_followup…")
    try:
        from database import SessionLocal
        from models import Client, Meeting
        from meeting_slots import DEFAULT_MEETING_DURATION_MIN
        from scheduler_utils import get_or_create_autotask

        with SessionLocal() as db:
            now = datetime.utcnow()
            # end_time окно [now-2h, now]; Meeting хранит только start (date),
            # поэтому фильтруем по date в окне [now - 2h - duration, now - duration].
            dur = timedelta(minutes=DEFAULT_MEETING_DURATION_MIN)
            lo = now - timedelta(hours=2) - dur
            hi = now - dur

            meetings = db.query(Meeting).filter(
                Meeting.date >= lo,
                Meeting.date <= hi,
            ).all()

            for m in meetings:
                if not m.date or not m.client_id:
                    continue
                if m.followup_status == "sent":
                    continue
                client = db.query(Client).filter(Client.id == m.client_id).first()
                if not client:
                    continue
                end_time = m.date + dur
                get_or_create_autotask(
                    db,
                    client_id=client.id,
                    rule_key=f"meeting_followup:{m.id}",
                    target_date=end_time.date(),
                    manager_email=client.manager_email,
                    title=f"Отправить фолоуап + заполнить Roadmap: {client.name}",
                    task_type="meeting_followup",
                    due_date=end_time + timedelta(hours=1),
                    meta={"meeting_id": m.id},
                    priority="high",
                )
            db.commit()
            logger.info(f"✅ hourly_meeting_followup: {len(meetings)} meetings scanned")
    except Exception as e:
        logger.error(f"❌ job_hourly_meeting_followup: {e}")


# Lead-дни до checkup_due per-segment: 180−5, 90−5, 60−5, 30−5.
_CHECKUP_SEGMENT_DAYS = {"SS": 175, "SMB": 85, "SME": 55, "ENT": 25,
                         "SME+": 55, "SME-": 55}


async def job_daily_checkup_due():
    """Ежедневно 09:00 МСК: «Сделать чекап ... (срок через 5 дней)» per-сегмент."""
    logger.info("🩺 daily_checkup_due…")
    try:
        from database import SessionLocal
        from models import Client, Task
        from scheduler_utils import get_or_create_autotask

        with SessionLocal() as db:
            now = datetime.utcnow()
            today = datetime.now(MSK).date()
            clients = db.query(Client).all()
            triggered = 0
            for c in clients:
                lead = _CHECKUP_SEGMENT_DAYS.get(c.segment or "")
                if not lead:
                    continue
                # Если last_checkup пуст — считаем от created_at (first checkup due).
                baseline = c.last_checkup or None
                if not baseline:
                    baseline = db.execute(
                        __import__("sqlalchemy").text(
                            "SELECT created_at FROM clients WHERE id = :id"
                        ), {"id": c.id},
                    ).scalar()
                if not baseline:
                    continue
                days_since = (now - baseline).days if isinstance(baseline, datetime) else (now.date() - baseline).days
                if days_since < lead:
                    continue

                # Dedupe: не чаще 1 раза в 30 дней по этому клиенту (любой rule_key
                # checkup_due:*).
                recent = db.query(Task).filter(
                    Task.client_id == c.id,
                    Task.meta["rule_key"].astext.like("checkup_due:%"),
                    Task.created_at >= now - timedelta(days=_CHECKUP_DUE_RECENT_GUARD_DAYS),
                ).first()
                if recent:
                    continue

                rule_key = f"checkup_due:{today.isoformat()}"
                get_or_create_autotask(
                    db,
                    client_id=c.id,
                    rule_key=rule_key,
                    target_date=today,
                    manager_email=c.manager_email,
                    title=f"Сделать чекап по клиенту {c.name} (срок через {_CHECKUP_DUE_LEAD_DAYS} дней)",
                    task_type="checkup_due",
                    due_date=now + timedelta(days=_CHECKUP_DUE_LEAD_DAYS),
                    meta={"segment": c.segment},
                    priority="high",
                )
                triggered += 1
            db.commit()
            logger.info(f"✅ daily_checkup_due: {triggered} tasks created ({len(clients)} clients scanned)")
    except Exception as e:
        logger.error(f"❌ job_daily_checkup_due: {e}")


async def job_daily_qbr_sync():
    """Ежедневно 09:00 МСК: «Согласовать QBR» за 7 дней до +90 от last_qbr_date."""
    logger.info("📊 daily_qbr_sync…")
    try:
        from database import SessionLocal
        from models import Client
        from scheduler_utils import get_or_create_autotask

        with SessionLocal() as db:
            today = datetime.now(MSK).date()
            now = datetime.utcnow()
            # Первый QBR не триггерим — нужен last_qbr_date.
            clients = db.query(Client).filter(Client.last_qbr_date.isnot(None)).all()
            triggered = 0
            for c in clients:
                # Если next_qbr_date уже задан — QBR запланирован, пропускаем.
                if c.next_qbr_date:
                    continue
                since_last = (now - c.last_qbr_date).days
                if since_last < _QBR_TRIGGER_OFFSET_DAYS:
                    continue
                rule_key = f"qbr_sync:{c.last_qbr_date.date().isoformat()}"
                get_or_create_autotask(
                    db,
                    client_id=c.id,
                    rule_key=rule_key,
                    target_date=today,
                    manager_email=c.manager_email,
                    title=f"Согласовать встречу по QBR с {c.name}",
                    task_type="qbr_sync",
                    due_date=now + timedelta(days=7),
                    meta={"last_qbr_date": c.last_qbr_date.isoformat()},
                    priority="high",
                )
                triggered += 1
            db.commit()
            logger.info(f"✅ daily_qbr_sync: {triggered} tasks")
    except Exception as e:
        logger.error(f"❌ job_daily_qbr_sync: {e}")


async def job_daily_onboarding_tick():
    """Ежедневно 09:00 МСК: создаёт задачи «Отправить сообщение по онбордингу #N»
    для всех активных OnboardingProgress, у которых next_send_date <= today."""
    logger.info("📧 daily_onboarding_tick…")
    try:
        from database import SessionLocal
        from models import Client, ClientOnboardingProgress
        from scheduler_utils import get_or_create_autotask

        with SessionLocal() as db:
            today = datetime.now(MSK).date()
            rows = (db.query(ClientOnboardingProgress)
                      .filter(ClientOnboardingProgress.completed_at.is_(None),
                              ClientOnboardingProgress.next_send_date <= today)
                      .all())
            created = 0
            for prog in rows:
                if prog.current_step >= 10:
                    continue
                next_step = prog.current_step + 1
                client = db.query(Client).filter(Client.id == prog.client_id).first()
                if not client:
                    continue
                get_or_create_autotask(
                    db,
                    client_id=client.id,
                    rule_key=f"onboarding_msg:{next_step}",
                    target_date=today,
                    manager_email=client.manager_email,
                    title=f"Отправить сообщение по онбордингу #{next_step}",
                    task_type="onboarding_message",
                    due_date=datetime.combine(today, datetime.min.time()) + timedelta(hours=18),
                    meta={"step": next_step, "onboarding_id": prog.id},
                    priority="medium",
                )
                created += 1
            db.commit()
            logger.info(f"✅ daily_onboarding_tick: {created} tasks created ({len(rows)} active)")
    except Exception as e:
        logger.error(f"❌ job_daily_onboarding_tick: {e}")


async def job_backup_all_managers():
    logger.info("🗄  Daily backup job starting")
    from database import SessionLocal
    from backups import backup_all_managers, cleanup_old_backups
    db = SessionLocal()
    try:
        paths = backup_all_managers(db)
        removed = cleanup_old_backups(keep_days=30)
        logger.info("✅ Backup done: %d files, purged %d old", len(paths), removed)
    finally:
        db.close()


def start_scheduler():
    sched = _get_scheduler()

    # Синки данных
    sched.add_job(job_sync_merchrules, "interval", hours=1,
                  id="sync_merchrules", name="Sync Merchrules (all users)", replace_existing=True)

    if os.environ.get("AIRTABLE_TOKEN") or os.environ.get("AIRTABLE_PAT"):
        sched.add_job(job_sync_airtable_clients, "interval", hours=1,
                      id="sync_airtable", name="Sync Airtable", replace_existing=True)

    sched.add_job(job_sync_meetings_and_slots, "interval", minutes=30,
                  id="sync_meetings_slots", name="Sync Ktalk/Outlook meetings", replace_existing=True)

    # Ежедневные
    sched.add_job(job_check_overdue_checkups, "cron", hour=8, minute=0,
                  id="check_overdue", name="Check Overdue Checkups", replace_existing=True)
    sched.add_job(job_deadline_reminders, "cron", hour=8, minute=30,
                  id="deadline_reminders", name="Deadline Reminders TG", replace_existing=True)
    sched.add_job(job_morning_plan, "cron", hour=9, minute=0, day_of_week="mon-fri",
                  id="morning_plan", name="Morning Plan TG", replace_existing=True)

    # Еженедельный
    sched.add_job(job_weekly_digest, "cron", hour=17, minute=0, day_of_week="fri",
                  id="weekly_digest", name="Weekly Digest", replace_existing=True)
    sched.add_job(job_health_recalc_all, "cron", hour=3, minute=0,
                  id="health_recalc", name="Nightly Health Recalc", replace_existing=True)
    sched.add_job(job_ktalk_sync, "interval", hours=2,
                  id="ktalk_sync", name="Ktalk Meeting Sync", replace_existing=True)
    sched.add_job(job_renewal_alerts, "cron", hour=9, minute=30, day_of_week="mon",
                  id="renewal_alerts", name="Weekly Renewal Alerts", replace_existing=True)

    sched.add_job(evaluate_auto_rules, "interval", hours=1,
                  id="evaluate_auto_rules", name="Evaluate Auto Task Rules", replace_existing=True)

    # Ported from main: user-defined auto-task rules with actions engine
    sched.add_job(job_auto_task_rules, "interval", hours=1,
                  id="auto_task_rules", name="Auto Task Rules (actions engine)", replace_existing=True)

    # Tbank Time support tickets ingest (Mattermost channel)
    sched.add_job(job_sync_tbank_tickets, "interval", minutes=15,
                  id="sync_tbank_tickets", name="Sync Tbank Time tickets", replace_existing=True)

    # Quarterly QBR auto-collect from Merchrules
    sched.add_job(job_qbr_auto_collect, "cron", day=1, hour=6, minute=0,
                  id="qbr_auto_collect", name="Quarterly QBR auto-collect", replace_existing=True)

    # Daily per-manager backups at 03:00 MSK
    sched.add_job(job_backup_all_managers, "cron", hour=3, minute=0,
                  id="daily_backup", name="Daily per-manager backups", replace_existing=True)

    # Autotasks: meeting prep/followup, checkup due, QBR sync, onboarding
    sched.add_job(job_meeting_reminder_30min, "interval", minutes=15,
                  id="meeting_reminder_30min", name="Meeting reminder (T−30min)", replace_existing=True)
    sched.add_job(job_daily_meeting_prep, "cron", hour=10, minute=0,
                  id="meeting_prep", name="Daily Meeting Prep 10:00 MSK", replace_existing=True)
    sched.add_job(job_hourly_meeting_followup, "interval", hours=1,
                  id="meeting_followup", name="Hourly Meeting Followup", replace_existing=True)
    sched.add_job(job_daily_checkup_due, "cron", hour=9, minute=0,
                  id="checkup_due", name="Daily Checkup Due (per-segment −5d)", replace_existing=True)
    sched.add_job(job_daily_qbr_sync, "cron", hour=9, minute=5,
                  id="qbr_sync", name="Daily QBR Sync (−7d before +3mo)", replace_existing=True)
    sched.add_job(job_daily_onboarding_tick, "cron", hour=9, minute=10,
                  id="onboarding_tick", name="Daily Onboarding Tick", replace_existing=True)

    sched.start()
    logger.info(f"✅ Scheduler started: {[j.id for j in sched.get_jobs()]}")


if __name__ == "__main__":
    import logging, asyncio
    logging.basicConfig(level=logging.INFO)
    start_scheduler()
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        _get_scheduler().shutdown()

async def job_telegram_notifications():
    """Ежедневные умные Telegram уведомления (9:00)."""
    from database import SessionLocal
    from models import User, Client, Task, TelegramSubscription
    from telegram_bot import send_daily_digest, notify_overdue_checkup, notify_task_overdue
    import os

    hub_url = os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if hub_url and not hub_url.startswith("http"):
        hub_url = "https://" + hub_url

    db = SessionLocal()
    try:
        subs = db.query(TelegramSubscription).filter(
            TelegramSubscription.is_active == True,
            TelegramSubscription.notify_daily == True
        ).all()

        now = datetime.now()
        from models import CHECKUP_INTERVALS

        for sub in subs:
            user = sub.user
            if not user or not user.is_active:
                continue

            q = db.query(Client)
            if user.role == "manager":
                q = q.filter(Client.manager_email == user.email)
            clients = q.all()

            overdue_checkups = 0
            overdue_names = []
            for c in clients:
                interval = CHECKUP_INTERVALS.get(c.segment or "", 90)
                last = c.last_meeting_date or c.last_checkup
                if last and (now - last).days > interval:
                    overdue_checkups += 1
                    overdue_names.append(c.name)

            # Задачи к сегодняшнему дедлайну
            today_end = now.replace(hour=23, minute=59)
            tq = db.query(Task).join(Client, Task.client_id == Client.id, isouter=True).filter(
                Task.status != "done",
                Task.due_date <= today_end
            )
            if user.role == "manager":
                tq = tq.filter(Client.manager_email == user.email)
            tasks_due = tq.all()

            # Встречи сегодня
            from models import Meeting
            mq = db.query(Meeting).join(Client, Meeting.client_id == Client.id, isouter=True).filter(
                Meeting.date >= now.replace(hour=0, minute=0),
                Meeting.date <= today_end
            )
            if user.role == "manager":
                mq = mq.filter(Client.manager_email == user.email)
            meetings_today = mq.count()

            health_crit = sum(1 for c in clients if (c.health_score or 0) < 40)

            import asyncio
            asyncio.create_task(send_daily_digest(sub.chat_id, {
                "overdue_checkups": overdue_checkups,
                "tasks_due_today": len(tasks_due),
                "health_critical": health_crit,
                "meetings_today": meetings_today,
            }, hub_url))
    finally:
        db.close()


