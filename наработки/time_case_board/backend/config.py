from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_ROOT = Path(__file__).resolve().parents[1]
_load_dotenv(_ROOT / "oauth.env")
_load_dotenv(_ROOT / ".env")

PROJECT_ROOT = _ROOT


def merge_project_dotenv(updates: dict[str, str]) -> None:
    """Дописать/заменить ключи в .env в корне проекта (не в git)."""
    path = PROJECT_ROOT / ".env"
    keys_upper = {k.upper() for k in updates}
    lines_out: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            k0 = line.split("=", 1)[0].strip()
            if k0.upper() in keys_upper:
                continue
            lines_out.append(line)
    for k, v in updates.items():
        safe = (v or "").replace("\r", "").replace("\n", " ")
        lines_out.append(f'{k}="{safe}"')
    path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    time_base_url: str = "https://time.tbank.ru"
    app_host: str = "127.0.0.1"
    app_port: int = 8790

    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://127.0.0.1:8790/oauth/callback"

    time_personal_access_token: str = ""

    # Jira (корп. REST v2, Bearer — только локально в oauth.env / env, не в git)
    jira_base_url: str = ""
    jira_token: str = ""
    jira_jql: str = (
        "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
    )

    # Каталог локальных данных (не в git). Доски: board_<account_id>.sqlite
    data_dir: Path = _ROOT / "data"
    # Старый путь одной БД — только для одноразовой миграции в board_<id>.sqlite
    database_path: Path = _ROOT / "data" / "time_case_board.db"

    @property
    def time_api_base(self) -> str:
        return self.time_base_url.rstrip("/") + "/api/v4"

    @property
    def oauth_authorize_url(self) -> str:
        return self.time_base_url.rstrip("/") + "/oauth/authorize"

    @property
    def oauth_token_url(self) -> str:
        return self.time_base_url.rstrip("/") + "/oauth/access_token"


settings = Settings()


def apply_jira_runtime(jira_base_url: str, jira_token: str) -> None:
    """Обновить os.environ и текущий settings без перезапуска uvicorn."""
    bu = (jira_base_url or "").strip().rstrip("/")
    tok = (jira_token or "").strip()
    os.environ["JIRA_BASE_URL"] = bu
    os.environ["JIRA_TOKEN"] = tok
    object.__setattr__(settings, "jira_base_url", bu)
    object.__setattr__(settings, "jira_token", tok)
