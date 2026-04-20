from fastapi.testclient import TestClient

from backend.main import app


def test_health():
    c = TestClient(app)
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_auth_status():
    c = TestClient(app)
    r = c.get("/api/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert "logged_in" in body


def test_jira_status_without_config(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "jira_base_url", "")
    monkeypatch.setattr(settings, "jira_token", "")
    c = TestClient(app)
    r = c.get("/api/jira/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["jira_host"] is None
    assert body.get("jira_account_id") is None


def test_history_pull_routes_exist_not_404():
    c = TestClient(app)
    for path in ("/api/history/pull", "/api/sync/history"):
        r = c.post(path, json={"pages": 2})
        assert r.status_code != 404
        if r.status_code == 200:
            assert "new_cases" in r.json()


def test_jira_issues_503_when_unconfigured(monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "jira_base_url", "")
    monkeypatch.setattr(settings, "jira_token", "")
    c = TestClient(app)
    r = c.get("/api/jira/issues")
    assert r.status_code == 503
