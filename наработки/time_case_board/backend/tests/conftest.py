import pytest

from backend.accounts import board_db_path, clear_pat_me_cache
from backend.config import settings
from backend.database import clear_engine_cache, get_engine


@pytest.fixture(autouse=True)
def _test_isolated_board_db(tmp_path, monkeypatch):
    """Локальная data_dir и одна тестовая доска — без PAT и без реального TiMe."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "time_personal_access_token", "")
    monkeypatch.setattr(
        "backend.accounts.resolve_active_board_account_id",
        lambda: "test_fixture_user",
    )
    clear_pat_me_cache()
    clear_engine_cache()
    get_engine(board_db_path("test_fixture_user"))
    yield
    clear_engine_cache()
    clear_pat_me_cache()
