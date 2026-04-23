"""Движок действий для AutoTaskRule."""
from datetime import datetime, timedelta
from typing import Dict, Any, List
import logging
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# Поддерживаемые типы действий:
#   {"type":"create_task",  "params":{"title":"...","description":"...","priority":"high","due_days":3,"task_type":"followup"}}
#   {"type":"create_note",  "params":{"content":"..."}}
#   {"type":"notify",       "params":{"channel":"email|tg|inapp","title":"...","message":"..."}}


def _substitute(text: str, ctx: Dict[str, Any]) -> str:
    """Подстановка {client_name}, {mrr}, {health_score}, {meeting_date}, {manager_email}, {task_title}, {nps}, {segment}."""
    if not text:
        return text
    out = text
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v) if v is not None else "")
    return out


def build_context(client, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ctx = {
        "client_name": getattr(client, "name", "") if client else "",
        "domain": getattr(client, "domain", "") if client else "",
        "segment": getattr(client, "segment", "") if client else "",
        "manager_email": getattr(client, "manager_email", "") if client else "",
        "health_score": getattr(client, "health_score", "") if client else "",
        "mrr": getattr(client, "mrr", "") if client else "",
        "nps": getattr(client, "nps_last", "") if client else "",
    }
    if extra:
        ctx.update({k: (v or "") for k, v in extra.items()})
    return ctx


def _create_delayed_task(client_id, title, description, priority, task_type, due_date, rule_name):
    """APScheduler callback: создать Task в новой сессии БД."""
    try:
        from database import SessionLocal
        from models import Task
    except Exception:
        log.exception("_create_delayed_task: import error")
        return
    db = SessionLocal()
    try:
        db.add(Task(
            client_id=client_id,
            title=title,
            description=description,
            status="plan",
            priority=priority,
            due_date=due_date,
            source="auto_rule",
            task_type=task_type,
        ))
        db.commit()
        log.info("delayed task created: rule=%s title=%s", rule_name, title)
    except Exception:
        log.exception("_create_delayed_task: db error")
        db.rollback()
    finally:
        db.close()


def _schedule_delayed_task(
    client_id, title, description, priority, task_type, due_date,
    run_at, rule_id, dry_run,
) -> bool:
    """Запланировать отложенное создание задачи через APScheduler.

    Возвращает True, если джоба успешно добавлена в шедулер.
    """
    if dry_run:
        return True
    try:
        from scheduler import _get_scheduler
        sched = _get_scheduler()
        sched.add_job(
            _create_delayed_task,
            trigger="date",
            run_date=run_at,
            args=[client_id, title, description, priority, task_type, due_date, f"rule_{rule_id}"],
            id=f"delayed_task_rule{rule_id}_{int(run_at.timestamp())}",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        return True
    except Exception:
        log.exception("_schedule_delayed_task: cannot schedule")
        return False


def execute_actions(db: Session, rule, client, extra_ctx: Dict[str, Any] | None = None, dry_run: bool = False) -> List[Dict[str, Any]]:
    """Выполняет actions правила для клиента. Возвращает отчёт."""
    from models import Task, ClientNote, Notification
    ctx = build_context(client, extra_ctx)
    results = []
    actions = rule.actions or []

    # Backward-compat: если actions пустой, но есть старые поля — сгенерируй create_task из них
    if not actions and getattr(rule, "task_title", None):
        actions = [{
            "type": "create_task",
            "params": {
                "title": rule.task_title,
                "description": rule.task_description or "",
                "priority": rule.task_priority or "medium",
                "due_days": rule.task_due_days or 3,
                "task_type": rule.task_type or "followup",
            }
        }]

    for a in actions:
        atype = a.get("type")
        p = a.get("params", {}) or {}
        try:
            if atype == "create_task":
                title = _substitute(p.get("title", ""), ctx)
                desc = _substitute(p.get("description", ""), ctx)
                desc = f"[Автозадача: {rule.name}]\n{desc}" if desc else f"[Автозадача: {rule.name}]"
                due = datetime.utcnow() + timedelta(days=int(p.get("due_days", 3)))
                delay_days = int(p.get("delay_days", 0) or 0)

                if delay_days > 0:
                    # Отложенное создание — запланируем джобу через APScheduler.
                    run_at = datetime.utcnow() + timedelta(days=delay_days)
                    scheduled = _schedule_delayed_task(
                        client_id=client.id if client else None,
                        title=title,
                        description=desc,
                        priority=p.get("priority", "medium"),
                        task_type=p.get("task_type"),
                        due_date=due,
                        run_at=run_at,
                        rule_id=rule.id,
                        dry_run=dry_run,
                    )
                    results.append({
                        "action":    "create_task",
                        "title":     title,
                        "due":       due.isoformat(),
                        "delayed":   True,
                        "run_at":    run_at.isoformat(),
                        "scheduled": scheduled,
                    })
                    continue

                t = Task(
                    client_id=client.id if client else None,
                    title=title, description=desc, status="plan",
                    priority=p.get("priority", "medium"),
                    due_date=due, source="auto_rule",
                    task_type=p.get("task_type"),
                )
                if not dry_run:
                    db.add(t)
                results.append({"action": "create_task", "title": title, "due": due.isoformat()})
            elif atype == "create_note":
                content = _substitute(p.get("content", ""), ctx)
                note = ClientNote(client_id=client.id if client else None, content=f"[Автоправило: {rule.name}]\n{content}")
                if not dry_run:
                    db.add(note)
                results.append({"action": "create_note", "content": content[:100]})
            elif atype == "notify":
                title = _substitute(p.get("title", ""), ctx)
                msg = _substitute(p.get("message", ""), ctx)
                # inapp-уведомление — запишем в таблицу Notification для менеджера клиента
                if client and client.manager_email:
                    from models import User
                    u = db.query(User).filter(User.email == client.manager_email).first()
                    if u:
                        n = Notification(user_id=u.id, title=title or rule.name, message=msg, type=p.get("channel", "inapp"))
                        if not dry_run:
                            db.add(n)
                results.append({"action": "notify", "channel": p.get("channel", "inapp"), "message": msg[:100]})
            else:
                results.append({"action": atype, "skipped": "unknown_action_type"})
        except Exception as e:
            log.exception("auto_action error rule=%s action=%s", rule.id, atype)
            results.append({"action": atype, "error": str(e)})

    # Обновим счётчики
    if not dry_run:
        rule.trigger_count = (rule.trigger_count or 0) + 1
        rule.last_triggered_at = datetime.utcnow()
        db.commit()

    return results


def match_trigger(rule, event: str, client, payload: Dict[str, Any] | None = None) -> bool:
    """Проверяет, подходит ли правило под событие."""
    if not rule.is_active:
        return False
    if rule.trigger != event:
        return False
    segs = rule.segment_filter or []
    if segs and client and client.segment not in segs:
        return False
    cfg = rule.trigger_config or {}
    payload = payload or {}
    if event == "meeting_done":
        types = cfg.get("meeting_types") or []
        if types and payload.get("meeting_type") not in types:
            return False
    elif event == "followup_sent":
        # delay_days обрабатывается внутри execute_actions (_schedule_delayed_task).
        pass
    return True


def fire_event(db: Session, event: str, client, payload: Dict[str, Any] | None = None):
    """Вызывается из хуков при meeting_done / followup_sent / task_status_change."""
    from models import AutoTaskRule
    q = db.query(AutoTaskRule).filter(AutoTaskRule.is_active == True, AutoTaskRule.trigger == event)
    # manager-scope: user_id is null (глобальное) или привязан к менеджеру клиента
    rules = q.all()
    for r in rules:
        try:
            if match_trigger(r, event, client, payload):
                execute_actions(db, r, client, extra_ctx=payload or {})
        except Exception:
            log.exception("fire_event error rule=%s", r.id)
