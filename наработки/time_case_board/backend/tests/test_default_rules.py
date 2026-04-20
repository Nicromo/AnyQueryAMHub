import json

from backend.sync_service import default_rules_json


def test_default_rules_empty_reporter_when_no_me():
    r = json.loads(default_rules_json(None))
    assert r.get("reporter_substrings") == []


def test_default_rules_from_me_no_hardcoded_names():
    me = {
        "username": "alice",
        "first_name": "Alice",
        "last_name": "Cooper",
        "nickname": "ac",
    }
    r = json.loads(default_rules_json(me))
    subs = r.get("reporter_substrings") or []
    assert "@alice" in subs
    blob = json.dumps(subs).lower()
    assert "zaporozhets" not in blob
    assert "zaporohzets" not in blob
