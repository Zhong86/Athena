import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway DB before anything imports the settings cache.
_TMP = Path(tempfile.mkdtemp()) / "test.db"
os.environ["SQLITE_PATH"] = str(_TMP)

from fastapi.testclient import TestClient  # noqa: E402

from agent import hermes  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def test_create_and_get_session(client):
    resp = client.post("/sessions", json={"type": "chat"})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["type"] == "chat"
    assert created["payload"] is None
    assert created["started_at"]

    got = client.get(f"/sessions/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]


def test_get_missing_session_404(client):
    assert client.get("/sessions/99999").status_code == 404


def test_rejects_invalid_type(client):
    assert client.post("/sessions", json={"type": "bogus"}).status_code == 422


def test_list_filters_by_type_and_paginates(client):
    for t in ("quiz", "cron", "agent_action"):
        client.post("/sessions", json={"type": t})

    everything = client.get("/sessions").json()
    assert everything["total"] >= 4

    quizzes = client.get("/sessions", params={"type": "quiz"}).json()
    assert quizzes["total"] == 1
    assert all(i["type"] == "quiz" for i in quizzes["items"])

    page = client.get("/sessions", params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2
    # newest first
    ids = [i["id"] for i in page["items"]]
    assert ids == sorted(ids, reverse=True)


def test_chat_persists_transcript(client, monkeypatch):
    async def fake_run(
        prompt, *, session_id=None, instructions=None, conversation_history=None, timeout=900.0
    ):
        fake_run.seen = {
            "prompt": prompt,
            "session_id": session_id,
            "instructions": instructions,
            "conversation_history": conversation_history,
        }
        return hermes.RunResult(
            run_id="run-1",
            status="completed",
            output="Entropy measures disorder.",
            trace=[{"kind": "tool", "tool": "search_materials", "status": "ok"}],
        )

    monkeypatch.setattr(hermes, "run", fake_run)

    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    resp = client.post(f"/sessions/{sid}/chat", json={"message": "what is entropy?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "Entropy measures disorder."
    assert body["trace"][0]["tool"] == "search_materials"

    # our session id is forwarded for correlation, and only the new message is
    # the prompt -- prior turns (none yet) travel as conversation_history
    assert fake_run.seen["session_id"] == str(sid)
    assert fake_run.seen["instructions"]
    assert fake_run.seen["prompt"] == "what is entropy?"
    assert fake_run.seen["conversation_history"] == []

    stored = client.get(f"/sessions/{sid}").json()["payload"]["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert all("at" in m for m in stored)
    assert stored[1]["trace"][0]["tool"] == "search_materials"

    # second turn must send the prior transcript back as history, not as the prompt
    client.post(f"/sessions/{sid}/chat", json={"message": "and enthalpy?"})
    assert fake_run.seen["prompt"] == "and enthalpy?"
    roles = [m["role"] for m in fake_run.seen["conversation_history"]]
    assert roles == ["user", "assistant"]
    # stored metadata must not leak into the Hermes payload
    assert all(set(m) == {"role", "content"} for m in fake_run.seen["conversation_history"])

    stored = client.get(f"/sessions/{sid}").json()["payload"]["messages"]
    assert len(stored) == 4


def test_chat_on_missing_session_404(client):
    assert client.post("/sessions/99999/chat", json={"message": "hi"}).status_code == 404


def test_chat_on_non_chat_session_409(client):
    sid = client.post("/sessions", json={"type": "quiz"}).json()["id"]
    assert client.post(f"/sessions/{sid}/chat", json={"message": "hi"}).status_code == 409


def test_chat_rejects_empty_message(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    assert client.post(f"/sessions/{sid}/chat", json={"message": ""}).status_code == 422


def test_hermes_failure_is_502_and_does_not_persist(client, monkeypatch):
    async def boom(
        prompt, *, session_id=None, instructions=None, conversation_history=None, timeout=900.0
    ):
        raise hermes.HermesError("connection refused")

    monkeypatch.setattr(hermes, "run", boom)

    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    resp = client.post(f"/sessions/{sid}/chat", json={"message": "hello"})
    assert resp.status_code == 502
    # a failed turn must leave no half-written transcript behind
    assert client.get(f"/sessions/{sid}").json()["payload"] is None


def _ids(client, **params):
    return [s["id"] for s in client.get("/sessions", params=params).json()["items"]]


def test_rename_and_clear_title(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]

    renamed = client.patch(f"/sessions/{sid}", json={"title": "  Entropy notes  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Entropy notes"

    # an explicit null drops back to the frontend-derived title
    assert client.patch(f"/sessions/{sid}", json={"title": None}).json()["title"] is None


def test_rename_rejects_blank_title(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    assert client.patch(f"/sessions/{sid}", json={"title": "   "}).status_code == 422


def test_archive_hides_from_log_and_count(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    before = client.get("/sessions", params={"type": "chat"}).json()["total"]

    archived = client.patch(f"/sessions/{sid}", json={"archived": True}).json()
    assert archived["archived_at"]

    page = client.get("/sessions", params={"type": "chat"}).json()
    assert sid not in [s["id"] for s in page["items"]]
    # total has to move with the rows, or the header lies about the list
    assert page["total"] == before - 1
    assert sid in _ids(client, type="chat", archived=True)

    # ...and unarchiving puts it back
    assert client.patch(f"/sessions/{sid}", json={"archived": False}).json()["archived_at"] is None
    assert sid in _ids(client, type="chat")


def test_patch_leaves_unsent_fields_alone(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    client.patch(f"/sessions/{sid}", json={"title": "Keep me"})
    client.patch(f"/sessions/{sid}", json={"archived": True})
    assert client.get(f"/sessions/{sid}").json()["title"] == "Keep me"


def test_delete_session(client):
    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    assert client.delete(f"/sessions/{sid}").status_code == 204
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert client.delete(f"/sessions/{sid}").status_code == 404


def test_patch_missing_session_404(client):
    assert client.patch("/sessions/99999", json={"title": "x"}).status_code == 404
