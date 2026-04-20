"""
scope.py — расчёт scope видимости клиентов на основе роли и активного scope.

Роли:
  manager    — только свои клиенты (всегда 'mine').
  grouphead  — 'mine' | 'group' (default 'group').
  leadership — 'group' (если group_id задан) | 'all' (default).
  admin      — 'mine' | 'group' (если group_id) | 'all' (default).
  viewer     — 'all' read-only (legacy; считаем как admin read).

Активный scope читается из cookie `am_scope` и валидируется по роли.
Возвращаемое значение get_manager_emails_for_scope:
  None                — фильтрация не нужна (все клиенты).
  list[str]           — список manager_email для WHERE-фильтра.
"""
from typing import List, Optional

from sqlalchemy.orm import Session


def available_scopes(user) -> List[str]:
    role = (user.role or "manager").lower()
    if role == "manager":
        return ["mine"]
    if role == "grouphead":
        return ["mine", "group"]
    if role == "leadership":
        return ["group", "all"] if user.group_id else ["all"]
    if role == "admin":
        return ["mine", "group", "all"] if user.group_id else ["mine", "all"]
    if role == "viewer":
        return ["all"]
    return ["mine"]


def default_scope(user) -> str:
    role = (user.role or "manager").lower()
    if role == "manager":
        return "mine"
    if role == "grouphead":
        return "group"
    if role in ("leadership", "viewer", "admin"):
        return "all"
    return "mine"


def resolve_scope(user, requested: Optional[str]) -> str:
    allowed = available_scopes(user)
    if requested in allowed:
        return requested
    return default_scope(user)


def get_manager_emails_for_scope(db: Session, user, scope: str) -> Optional[List[str]]:
    """
    None  = фильтр не применяется (scope='all' с правом).
    list  = фильтр по client.manager_email IN (...).
    """
    if scope == "all":
        return None
    if scope == "mine":
        return [user.email] if user.email else []
    if scope == "group":
        if not user.group_id:
            return [user.email] if user.email else []
        from models import User
        emails = [e for (e,) in db.query(User.email)
                  .filter(User.group_id == user.group_id, User.is_active == True)
                  .all() if e]
        return emails or [user.email or ""]
    return [user.email] if user.email else []


def can_edit(user) -> bool:
    """leadership — read-only, остальные активные роли могут писать."""
    role = (user.role or "manager").lower()
    if role == "leadership" or role == "viewer":
        return False
    return True
