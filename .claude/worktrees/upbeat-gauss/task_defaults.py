"""
Дефолты для полей задач дорожной карты при импорте.
Если в форме/CSV не указаны — подставляются эти значения.
"""
DEFAULT_STATUS = "plan"
DEFAULT_PRIORITY = "medium"
DEFAULT_ASSIGNEE = "any"  # в API: any = Диджинетика, partner = партнёр


def apply_task_defaults(fields: dict) -> dict:
    """Подставить дефолты для status, priority, assignee если пусто."""
    out = dict(fields)
    if not (out.get("status") or "").strip():
        out["status"] = DEFAULT_STATUS
    if not (out.get("priority") or "").strip():
        out["priority"] = DEFAULT_PRIORITY
    if not (out.get("assignee") or "").strip():
        out["assignee"] = DEFAULT_ASSIGNEE
    return out
