import os
import tempfile
from pathlib import Path

os.environ.setdefault("SQLITE_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))

from agent import hermes  # noqa: E402
from app.config import get_settings  # noqa: E402


def _reload_settings(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(
        hermes, "get_settings", get_settings, raising=False
    )


def test_no_authorization_header_when_key_is_unset(monkeypatch):
    """A bare 'Bearer ' is an illegal header value -- httpx raises on it, which
    surfaces as a confusing transport error instead of the gateway's 401."""
    _reload_settings(monkeypatch, API_SERVER_KEY="")
    headers = hermes._headers("7")
    assert "Authorization" not in headers
    get_settings.cache_clear()


def test_authorization_header_present_when_key_is_set(monkeypatch):
    _reload_settings(monkeypatch, API_SERVER_KEY="secret-abc")
    headers = hermes._headers("7")
    assert headers["Authorization"] == "Bearer secret-abc"
    get_settings.cache_clear()


def test_session_id_header_only_when_given(monkeypatch):
    _reload_settings(monkeypatch, API_SERVER_KEY="")
    assert hermes._headers("42")["X-Hermes-Session-Id"] == "42"
    assert "X-Hermes-Session-Id" not in hermes._headers()
    # long-term memory scope is always sent; it is fixed for this single user
    assert hermes._headers()["X-Hermes-Session-Key"]
    get_settings.cache_clear()
