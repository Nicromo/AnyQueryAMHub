from backend.parsers import (
    build_parsed_fields,
    matches_ds_ama_template,
    matches_support_template,
    parse_ds_ama_template,
    parse_support_template,
    post_matches_column_rules,
    support_intro_line_mentions_username,
)
from backend.url_parse import parse_channel_url, parse_post_permalink


def test_parse_post_and_channel_urls():
    p = parse_post_permalink("https://time.tbank.ru/tinkoff/pl/ggm6dsuesjrbjg5opf6nys54we")
    assert p and p.team_name == "tinkoff" and p.post_id == "ggm6dsuesjrbjg5opf6nys54we"
    c = parse_channel_url("https://time.tbank.ru/tinkoff/channels/any-ds-ama2")
    assert c and c.channel_name == "any-ds-ama2"


SUPPORT_SAMPLE = """К кому вопрос ?:
@any_2nd_line
Какой Site ID (Customer ID)?:
6321
Клиент из списка Топ-50 или в угрозе?:
Топ-50
Опишите кратко суть вопроса:
Можете проверить ограничения по выдаче?
Примеры кейсов, логи и ссылки на API (вложения оставляйте в треде):
Left empty"""

DS_SAMPLE = """Обращение в DS AMA от пользователя @Nikita Zaporozhets

Тематика вопроса:
Выпрямитель
Партнёр (siteID, апи, название, ссылка):
1487
Срочность / MRR:
250000
Опишите суть проблемы:
Привет, у партнера не корректируются запросы
Примеры:
https://example.com"""


def test_support_template():
    assert matches_support_template(SUPPORT_SAMPLE)
    d = parse_support_template(SUPPORT_SAMPLE)
    assert d.get("site_id") == "6321"
    assert "any_2nd_line" in (d.get("assignee_raw") or "")


def test_ds_ama_template():
    assert matches_ds_ama_template(DS_SAMPLE)
    d = parse_ds_ama_template(DS_SAMPLE)
    assert d.get("site_id") == "1487"
    assert "проблемы" in DS_SAMPLE


def test_reporter_substrings_bypass_when_tagged_in_metadata_only():
    """В тексте нет фамилии/@login, но user id в metadata.mentions — не режем reporter_substrings."""
    rules = {
        "templates": ["ds_ama"],
        "require_addressed_to_me": True,
        "match_mentions_me": True,
        "reporter_substrings": ["@n.i.zaporozhets", "zaporozhets"],
        "support_intro_required": False,
    }
    msg = """Обращение в DS AMA

Партнёр (siteID):
1487
Опишите суть проблемы:
просто вопрос без фамилии в тексте"""
    assert matches_ds_ama_template(msg)
    assert post_matches_column_rules(msg, rules, "n.i.zaporozhets", "uid-me", ["uid-me"])


def test_freeform_mention_any_channel():
    """b2b.xcom Site ID: 2598 @Nikita Zaporozhets ... — произвольный тег, нет шаблона."""
    # Правила как у Tech-колонки (require_addressed_to_me=false, match_mentions_me=false, reporter_substrings)
    rules = {
        "templates": ["support", "ds_ama"],
        "match_mentions_me": False,
        "require_addressed_to_me": False,
        "extra_names": [],
        "reporter_substrings": ["@n.i.zaporozhets", "nikita zaporozhets", "zaporozhets", "zaporohzets"],
        "support_intro_required": False,
        "match_self_only": False,
    }
    # Сообщение из скриншота: @DisplayName (не username), фамилия есть в тексте
    msg = "b2b.xcom\nSite ID: 2598\n@Nikita Zaporozhets в фиде сейчас сократилось кол во товаров"
    assert post_matches_column_rules(msg, rules, "n.i.zaporozhets", "uid-me", [])

    # Тег только через metadata.mentions (нет фамилии / username в тексте)
    msg2 = "b2b.xcom\nSite ID: 2598\n@Nikita в фиде сейчас сократилось кол во товаров"
    assert post_matches_column_rules(msg2, rules, "n.i.zaporozhets", "uid-me", ["uid-me"])


def test_match_self_only_skips_template_gate():
    """Свои посты в канале «только мои» — не требуют шаблона саппорта и @себя."""
    rules = {
        "match_self_only": True,
        "templates": ["support", "ds_ama"],
        "require_addressed_to_me": False,
    }
    assert post_matches_column_rules("просто текст задачи без шаблона", rules, "me", "uid1", [])


def test_post_matches_rules():
    rules = {"templates": ["support"], "match_mentions_me": False, "extra_names": []}
    assert not post_matches_column_rules(SUPPORT_SAMPLE, {**rules, "require_addressed_to_me": True}, "me", "uid1", [])
    # по умолчанию без ключа — тоже только с упоминанием
    assert not post_matches_column_rules(SUPPORT_SAMPLE, rules, "me", "uid1", [])
    rules_all = {**rules, "require_addressed_to_me": False}
    assert post_matches_column_rules(SUPPORT_SAMPLE, rules_all, "me", "uid1", [])
    assert not post_matches_column_rules(DS_SAMPLE, rules, "me", "uid1", [])

    rules2 = {"templates": [], "match_mentions_me": True, "extra_names": ["Nikita"]}
    assert post_matches_column_rules("hello @me there", rules2, "me", "uid1", [])


def test_support_intro_line_workflow_author():
    line = "Обращение в саппорт от пользователя @n.i.zaporozhets\n"
    assert support_intro_line_mentions_username(line, "n.i.zaporozhets")
    assert not support_intro_line_mentions_username(line, "other.person")
    assert support_intro_line_mentions_username(
        "Обращение в саппорт от пользователя @Nikita Zaporozhets\n", "nikita.zaporozhets"
    )


def test_require_addressed_needs_at_or_mention_meta():
    rules = {"templates": ["support"], "require_addressed_to_me": True, "extra_names": []}
    assert not post_matches_column_rules(SUPPORT_SAMPLE, rules, "me", "uid1", [])
    assert post_matches_column_rules(SUPPORT_SAMPLE, rules, "me", "uid1", ["uid1"])
    with_at = (
        "К кому вопрос ?:\n@x\nКакой Site ID (Customer ID)?:\n1\n"
        "Опишите кратко суть вопроса:\nhello @me\n"
    )
    assert post_matches_column_rules(with_at, rules, "me", "uid1", [])


WORKFLOW_SUPPORT = """Обращение в саппорт от пользователя @Nikita Zaporozhets

К кому вопрос ?:
@any_1st_line
Какой Site ID (Customer ID)?:
224
Клиент из списка Топ-50 или в угрозе?:
Топ-50
Опишите кратко суть вопроса:
Тест
Примеры кейсов, логи и ссылки на API (вложения оставляйте в треде):
-"""


def test_workflow_support_site_id():
    assert matches_support_template(WORKFLOW_SUPPORT)
    d = parse_support_template(WORKFLOW_SUPPORT)
    assert d.get("site_id") == "224"


def test_reporter_and_intro_filters():
    rules = {
        "templates": ["support"],
        "match_mentions_me": False,
        "extra_names": [],
        "reporter_substrings": ["nikita zaporozhets", "@nikita"],
        "support_intro_required": True,
    }
    assert post_matches_column_rules(WORKFLOW_SUPPORT, rules, "nikita", "y", [])
    rules_wrong_user = {**rules, "reporter_substrings": ["@someone_else"]}
    assert not post_matches_column_rules(WORKFLOW_SUPPORT, rules_wrong_user, "nikita", "y", [])
    rules_intro = {
        "templates": ["support"],
        "match_mentions_me": False,
        "extra_names": [],
        "support_intro_required": True,
    }
    assert not post_matches_column_rules(SUPPORT_SAMPLE, rules_intro, "me", "uid1", [])


def test_ds_ama_still_matches_with_intro_required():
    rules = {
        "templates": ["support", "ds_ama"],
        "match_mentions_me": False,
        "extra_names": [],
        "support_intro_required": True,
    }
    assert post_matches_column_rules(DS_SAMPLE, rules, "nikita", "y", [])


def test_build_parsed_fields():
    rules = {"templates": ["support", "ds_ama"]}
    f = build_parsed_fields(SUPPORT_SAMPLE, rules)
    assert "6321" in str(f.get("site_id"))
