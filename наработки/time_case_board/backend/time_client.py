from __future__ import annotations

from typing import Any

import httpx

from backend.config import settings


class TimeApiError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Time API {status}: {body[:500]}")


class TimeClient:
    def __init__(self, token: str):
        self._token = token
        self._base = settings.time_api_base

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self._base}{path}"
        with httpx.Client(timeout=60.0) as client:
            r = client.request(method, url, headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            raise TimeApiError(r.status_code, r.text)
        if not r.content:
            return None
        return r.json()

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/users/me")

    def get_team_by_name(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/teams/name/{name}")

    def get_channel_by_name(self, team_id: str, channel_name: str) -> dict[str, Any]:
        return self._request("GET", f"/teams/{team_id}/channels/name/{channel_name}")

    def get_post(self, post_id: str) -> dict[str, Any]:
        return self._request("GET", f"/posts/{post_id}")

    def get_post_thread(self, post_id: str) -> dict[str, Any]:
        return self._request("GET", f"/posts/{post_id}/thread")

    def get_posts_since(self, channel_id: str, since_ms: int) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": 200}
        if since_ms > 0:
            params["since"] = since_ms
        return self._request("GET", f"/channels/{channel_id}/posts", params=params)

    def get_posts_latest(self, channel_id: str, per_page: int = 200) -> dict[str, Any]:
        return self._request("GET", f"/channels/{channel_id}/posts", params={"per_page": per_page})

    def get_posts_before(self, channel_id: str, before_id: str, per_page: int = 200) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/channels/{channel_id}/posts",
            params={"before": before_id, "per_page": per_page},
        )

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self._request("GET", f"/users/{user_id}")

    def create_post(self, channel_id: str, message: str) -> dict[str, Any]:
        return self._request("POST", f"/channels/{channel_id}/posts", json={"message": message})


def extract_mention_user_ids(post: dict[str, Any]) -> list[str]:
    meta = post.get("metadata") or {}
    mentions = meta.get("mentions") or []
    if isinstance(mentions, list):
        return [str(x) for x in mentions]
    return []


def root_post_id_from(post: dict[str, Any]) -> str:
    rid = post.get("root_id") or ""
    if isinstance(rid, str) and rid.strip():
        return rid
    return post.get("id") or ""


def post_permalink(team_name: str, post_id: str) -> str:
    base = settings.time_base_url.rstrip("/")
    return f"{base}/{team_name}/pl/{post_id}"
