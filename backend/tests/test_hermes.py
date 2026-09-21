import os
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("SQLITE_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))

from agent import hermes  # noqa: E402
from app.config import get_settings  # noqa: E402
from scripts.fake_hermes import Handler  # noqa: E402


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


# ---------- Runs API ----------


def test_parse_sse_skips_keepalives_and_joins_multiline_data():
    lines = [
        ": keepalive",
        "",
        "event: message.interim",
        'data: {"text": "hello",',
        'data:  "already_streamed": false}',
        "",
        ": keepalive",
        "",
        "event: run.completed",
        'data: {"status": "completed"}',
        "",
    ]
    assert hermes.parse_sse(lines) == [
        ("message.interim", {"text": "hello", "already_streamed": False}),
        ("run.completed", {"status": "completed"}),
    ]


def test_parse_sse_drops_a_malformed_frame_but_keeps_the_rest():
    """One bad frame must not cost us the trace of an otherwise good run."""
    lines = [
        "event: message.interim",
        "data: {not json",
        "",
        "event: run.completed",
        'data: {"status": "completed"}',
        "",
    ]
    assert hermes.parse_sse(lines) == [("run.completed", {"status": "completed"})]


def test_fold_pairs_tool_start_with_its_completion():
    trace, pending = [], {}
    hermes._fold_event(trace, pending, "tool.started", {"tool": "grep", "preview": "foo"})
    hermes._fold_event(
        trace, pending, "tool.completed", {"tool": "grep", "duration": 1.5, "error": False, "preview": "3 hits"}
    )
    assert len(trace) == 1
    assert trace[0]["tool"] == "grep"
    assert trace[0]["input"] == "foo"
    assert trace[0]["status"] == "ok"
    assert trace[0]["duration"] == 1.5
    assert trace[0]["output"] == "3 hits"


def test_fold_handles_a_completion_with_no_start():
    """We subscribe after POST /v1/runs returns, so the start may be missed."""
    trace, pending = [], {}
    hermes._fold_event(trace, pending, "tool.completed", {"tool": "grep", "error": True})
    assert trace == [
        {"kind": "tool", "at": trace[0]["at"], "tool": "grep", "input": None,
         "status": "error", "duration": None, "output": None}
    ]


def test_fold_ignores_message_delta():
    """Deltas duplicate the run's `output`; replaying them doubles findings."""
    trace, pending = [], {}
    hermes._fold_event(trace, pending, "message.delta", {"text": "partial"})
    assert trace == []


@pytest.mark.anyio
async def test_run_against_the_fake_gateway(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _reload_settings(
            monkeypatch,
            API_SERVER_KEY="",
            HERMES_BASE_URL=f"http://127.0.0.1:{server.server_port}",
        )
        result = await hermes.run("What changed this week?", session_id="7")
    finally:
        server.shutdown()
        get_settings.cache_clear()

    assert result.run_id == "run_stub123"
    assert result.status == "completed"
    assert result.output.startswith("Two topics are going stale")

    kinds = [e["kind"] for e in result.trace]
    assert kinds == ["note", "tool", "note", "tool", "subagent"]
    assert [e["status"] for e in result.trace if e["kind"] == "tool"] == ["ok", "error"]
