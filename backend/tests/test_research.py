"""`app.goals.research.investigate` -- the one roadmap caller on the Runs API.

test_hermes.py already proves the SSE/trace parsing works end to end against
the fake gateway; this covers what `investigate` does with a `RunResult` once
it has one, so Hermes is stubbed at `agent.hermes.run` directly.
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("SQLITE_PATH", str(_TMP / "research.db"))

from agent import hermes  # noqa: E402
from app.goals import research  # noqa: E402


def _install(monkeypatch, result):
    async def run(prompt, *, session_id=None, instructions=None, timeout=900.0):
        return result

    monkeypatch.setattr("agent.hermes.run", run)


def test_investigate_uses_the_runs_output_and_tool_trace(monkeypatch):
    result = hermes.RunResult(
        run_id="run_1",
        status="completed",
        output=json.dumps(
            {
                "reason": "Eigenvectors show up in every later topic",
                "reason_long": "Diagonalization depends on them, and so does PCA later on.",
            }
        ),
        trace=[
            {"kind": "note", "text": "Checking recent sources."},
            {
                "kind": "tool",
                "tool": "web_search",
                "status": "ok",
                "output": "6 results; top hit is a linear algebra primer.",
            },
        ],
    )
    _install(monkeypatch, result)

    note = research.investigate("Eigenvectors", "Diagonalization.")

    assert note.reason == "Eigenvectors show up in every later topic"
    assert note.reason_long.startswith("Diagonalization depends")
    assert note.grounded
    assert note.sources == ("web_search: 6 results; top hit is a linear algebra primer.",)


def test_investigate_falls_back_when_hermes_is_unreachable(monkeypatch):
    async def run(prompt, *, session_id=None, instructions=None, timeout=900.0):
        raise hermes.HermesError("connection refused")

    monkeypatch.setattr("agent.hermes.run", run)

    note = research.investigate("Eigenvectors", "Diagonalization.")

    assert note.reason == research._FALLBACK_REASON
    assert note.reason_long == research._FALLBACK_REASON_LONG
    assert not note.grounded


def test_investigate_falls_back_when_the_run_errors_mid_trace(monkeypatch):
    """A `run.failed` event folds into a `kind: "error"` trace entry -- that must
    be treated as a failure even though the HTTP round-trip itself succeeded."""
    result = hermes.RunResult(
        run_id="run_2",
        status="failed",
        output="",
        trace=[{"kind": "error", "text": "tool crashed"}],
    )
    _install(monkeypatch, result)

    note = research.investigate("Eigenvectors", "Diagonalization.")

    assert note.reason == research._FALLBACK_REASON
    assert not note.grounded


def test_investigate_falls_back_on_unparseable_output(monkeypatch):
    result = hermes.RunResult(run_id="run_3", status="completed", output="not json", trace=[])
    _install(monkeypatch, result)

    note = research.investigate("Eigenvectors", "Diagonalization.")

    assert note.reason == research._FALLBACK_REASON
    assert not note.grounded


def test_investigate_ignores_a_tool_that_did_not_succeed(monkeypatch):
    """A source list built from every tool call, ok or not, would misrepresent
    what the run actually managed to consult."""
    result = hermes.RunResult(
        run_id="run_4",
        status="completed",
        output=json.dumps({"reason": "short", "reason_long": "long"}),
        trace=[
            {"kind": "tool", "tool": "read_file", "status": "error", "output": "not found"},
        ],
    )
    _install(monkeypatch, result)

    note = research.investigate("Eigenvectors", "Diagonalization.")

    assert note.sources == ()
    assert not note.grounded
