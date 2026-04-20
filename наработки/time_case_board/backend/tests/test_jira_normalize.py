from backend.jira_client import normalize_issue


def test_normalize_issue():
    issue = {
        "key": "FOO-1",
        "fields": {
            "summary": "Test",
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "updated": "2026-01-01T12:00:00.000+0300",
            "description": "d1",
            "comment": {
                "comments": [
                    {
                        "author": {"displayName": "Alice"},
                        "body": "First",
                        "created": "2025-12-01T10:00:00.000+0300",
                    },
                    {
                        "author": {
                            "displayName": "Bob",
                            "accountId": "acc-bob",
                            "name": "bob.user",
                        },
                        "body": "Latest note",
                        "created": "2026-01-02T15:00:00.000+0300",
                    },
                ]
            },
        },
    }
    out = normalize_issue(issue, "https://jira.example.com")
    assert out["key"] == "FOO-1"
    assert out["project_key"] == "FOO"
    assert out["summary"] == "Test"
    assert out["status"] == "Open"
    assert out["priority"] == "High"
    assert out["browse_url"] == "https://jira.example.com/browse/FOO-1"
    assert out["last_comment_author"] == "Bob"
    assert out["last_comment_author_account_id"] == "acc-bob"
    assert out["last_comment_author_name"] == "bob.user"
    assert "Latest" in out["last_comment_preview"]
    assert out["sort_timestamp"] == "2026-01-02T15:00:00.000+0300"
