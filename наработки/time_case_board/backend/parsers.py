from __future__ import annotations

import re
from typing import Any


def _line_after_label(text: str, label: str) -> str | None:
    """Return text after 'Label:' on same line or following lines until next '...:' header."""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if label.lower() in line.lower() and ":" in line:
            after = line.split(":", 1)[1].strip()
            if after:
                return after
            # multi-line value until next line that looks like a field
            buf: list[str] = []
            for j in range(i + 1, len(lines)):
                n = lines[j].strip()
                if not n:
                    if buf:
                        break
                    continue
                if re.match(r"^[^:\n]+:\s*$", n) or re.match(r"^[^:\n]+:\s+\S", n):
                    # next field
                    break
                buf.append(n)
            return "\n".join(buf).strip() if buf else None
    return None


def extract_site_id_loose(message: str) -> str | None:
    """Fallback: Customer / Site ID в одной строке или после метки (2–8 цифр)."""
    patterns = [
        r"(?:site\s*id|customer\s*id)\s*(?:\([^)]*\))?\s*[:：?？\s]*(\d{2,8})\b",
        r"какой\s+site\s+id[^\d\n]{0,50}(\d{2,8})\b",
    ]
    for pat in patterns:
        m = re.search(pat, message, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


def parse_support_template(message: str) -> dict[str, Any]:
    """Template 1: support-style form."""
    out: dict[str, Any] = {"template": "support"}
    site = _line_after_label(message, "Какой Site ID")
    if site:
        m = re.search(r"(\d{2,8})\b", site)
        out["site_id"] = m.group(1) if m else site.strip()
    if not out.get("site_id"):
        fb = extract_site_id_loose(message)
        if fb:
            out["site_id"] = fb
    assignee = _line_after_label(message, "К кому вопрос")
    if assignee:
        out["assignee_raw"] = assignee.strip()
    desc = _line_after_label(message, "Опишите кратко суть вопроса")
    if desc:
        out["description"] = desc.strip()[:2000]
    return out


def parse_ds_ama_template(message: str) -> dict[str, Any]:
    """Template 2: DS AMA style."""
    out: dict[str, Any] = {"template": "ds_ama"}
    block = _line_after_label(message, "Партнёр (siteID")
    if not block:
        block = _line_after_label(message, "Партнёр")
    if block:
        m = re.search(r"(\d{3,6})", block)
        if m:
            out["site_id"] = m.group(1)
    desc = _line_after_label(message, "Опишите суть проблемы")
    if desc:
        out["description"] = desc.strip()[:2000]
    user_line = None
    for line in message.splitlines():
        if "от пользователя" in line.lower() or "пользователя @" in line.lower():
            user_line = line
            break
    if user_line:
        out["assignee_raw"] = user_line.strip()
    return out


def matches_support_template(message: str) -> bool:
    m = message.lower()
    return "какой site id" in m and ("к кому вопрос" in m or "к кому вопрос?" in m)


def matches_ds_ama_template(message: str) -> bool:
    m = message.lower()
    return "обращение в ds ama" in m or "партнёр (siteid" in m or "партнер (siteid" in m


def _norm_mention_key(s: str) -> str:
    return re.sub(r"[\s._-]+", "", (s or "").lower())


def support_intro_line_mentions_username(message: str, my_username: str) -> bool:
    """
    Пост от Workflow: реальный автор в строке «Обращение в саппорт … от пользователя @…».
    Сравниваем с логином залогиненного пользователя (регистр, точки/дефисы/пробелы в @-хвосте).
    """
    if not my_username:
        return False
    uname = my_username.lower().strip()
    me_key = _norm_mention_key(uname)
    if len(me_key) < 2:
        return False
    for line in message.replace("\r\n", "\n").split("\n"):
        low = line.lower()
        if "обращение" not in low or "саппорт" not in low:
            continue
        if "пользовател" not in low:
            continue
        if f"@{uname}" in low:
            return True
        for m in re.finditer(r"@([\w._-]+(?:\s+[\w._-]+)?)", line, re.UNICODE):
            raw = m.group(1).strip()
            if _norm_mention_key(raw) == me_key:
                return True
        break
    return False


def addressed_to_me(
    root_message: str,
    my_username: str,
    my_user_id: str,
    mention_user_ids: list[str],
    rules: dict[str, Any],
) -> bool:
    """Текст явно адресован мне: @username, mentions в metadata, extra_names, строка саппорт-интро."""
    lower = root_message.lower()
    uname = (my_username or "").lower().strip()
    if uname and f"@{uname}" in lower:
        return True
    uid = my_user_id or ""
    if uid and uid in mention_user_ids:
        return True
    if support_intro_line_mentions_username(root_message, my_username):
        return True
    extra_names = rules.get("extra_names") or []
    for n in extra_names:
        if isinstance(n, str) and n.strip() and n.strip().lower() in lower:
            return True
    return False


def post_matches_column_rules(
    root_message: str,
    rules: dict[str, Any],
    my_username: str,
    my_user_id: str,
    mention_user_ids: list[str] | None = None,
) -> bool:
    """
    rules:
      templates: list[str]  e.g. ["support", "ds_ama"]; [] = не использовать шаблоны
      require_addressed_to_me: bool — только если меня @упомянули / в mentions / extra_names
      match_mentions_me: bool — в режиме без require_addressed: участвует в OR с шаблоном
      extra_names: list[str]  full name substrings
      reporter_substrings: list[str] — хотя бы одна подстрока должна быть в тексте (напр. @user, имя)
      support_intro_required: bool — только посты с «Обращение в саппорт»
    """
    mention_user_ids = list(mention_user_ids or [])
    # Корни от других авторов уже отсечены в sync_service; шаблоны саппорта к своим постам не применяем.
    if rules.get("match_self_only"):
        return True

    tlist = rules.get("templates")
    if tlist is None:
        tlist = ["support", "ds_ama"]
    elif not isinstance(tlist, list):
        tlist = ["support", "ds_ama"]

    lower = root_message.lower()

    if "require_addressed_to_me" in rules:
        require_addressed = bool(rules.get("require_addressed_to_me"))
    else:
        # без явного ключа — только треды с упоминанием меня (старый match_mentions_me=False больше не открывает «все формы»)
        require_addressed = True

    template_match = False
    for t in tlist:
        if t == "support" and matches_support_template(root_message):
            template_match = True
        if t == "ds_ama" and matches_ds_ama_template(root_message):
            template_match = True

    mention_me = bool(rules.get("match_mentions_me", True))
    mention_match = False
    if mention_me:
        uname = (my_username or "").lower().strip()
        if uname and f"@{uname}" in lower:
            mention_match = True
        if my_user_id and my_user_id in mention_user_ids:
            mention_match = True

    extra_names = rules.get("extra_names") or []
    name_match = any(n.strip() and n.strip().lower() in lower for n in extra_names if isinstance(n, str))

    rep = rules.get("reporter_substrings") or []
    rep_l = [r.strip().lower() for r in rep if isinstance(r, str) and r.strip()]
    # Тег через metadata: user_id в mentions (когда @DisplayName без @username в тексте)
    tagged_in_meta = bool(my_user_id and my_user_id in (mention_user_ids or []))
    reporter_match = bool(rep_l and (any(r in lower for r in rep_l) or tagged_in_meta))

    # Прямой @username-тег в тексте или через Mattermost metadata
    uname_l = (my_username or "").lower().strip()
    direct_tag = bool(uname_l and f"@{uname_l}" in lower) or tagged_in_meta

    if require_addressed:
        if not addressed_to_me(root_message, my_username, my_user_id, mention_user_ids, rules):
            return False
        if not tlist:
            return True
        # Прямой тег (@username в тексте или user_id в metadata) — шаблон не нужен
        if not direct_tag and not template_match:
            return False
    else:
        if tlist:
            # Без require_addressed: пропускаем если шаблон ИЛИ упоминание ИЛИ имя ИЛИ reporter_substrings
            if not (template_match or mention_match or name_match or reporter_match or direct_tag):
                return False
        # Если tlist пуст — пропускаем всё (фильтр отключён)

    if rep_l:
        if not (any(r in lower for r in rep_l) or tagged_in_meta):
            return False

    if rules.get("support_intro_required"):
        # Только для постов в форме саппорта — не режем AMA и произвольные теги
        if matches_support_template(root_message) and "обращение в саппорт" not in lower:
            return False

    return True


def build_parsed_fields(root_message: str, rules: dict[str, Any]) -> dict[str, Any]:
    templates = rules.get("templates") or ["support", "ds_ama"]
    merged: dict[str, Any] = {}
    if "support" in templates and matches_support_template(root_message):
        merged.update(parse_support_template(root_message))
    if "ds_ama" in templates and matches_ds_ama_template(root_message):
        ds = parse_ds_ama_template(root_message)
        for k, v in ds.items():
            if v and (k not in merged or not merged.get(k)):
                merged[k] = v
    if not merged.get("description"):
        merged["description"] = root_message.strip()[:280] + ("…" if len(root_message) > 280 else "")
    return merged
