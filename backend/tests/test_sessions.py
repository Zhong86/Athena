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
    async def fake_chat(messages, *, session_id=None, system=None):
        fake_chat.seen = {"messages": messages, "session_id": session_id, "system": system}
        return "Entropy measures disorder."

    monkeypatch.setattr(hermes, "chat", fake_chat)

    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    resp = client.post(f"/sessions/{sid}/chat", json={"message": "what is entropy?"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "Entropy measures disorder."

    # our session id is forwarded for correlation
    assert fake_chat.seen["session_id"] == str(sid)
    assert fake_chat.seen["system"]

    stored = client.get(f"/sessions/{sid}").json()["payload"]["messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert all("at" in m for m in stored)

    # second turn must send the prior transcript back
    client.post(f"/sessions/{sid}/chat", json={"message": "and enthalpy?"})
    roles = [m["role"] for m in fake_chat.seen["messages"]]
    assert roles == ["user", "assistant", "user"]
    # stored metadata must not leak into the Hermes payload
    assert all(set(m) == {"role", "content"} for m in fake_chat.seen["messages"])

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
    async def boom(messages, *, session_id=None, system=None):
        raise hermes.HermesError("connection refused")

    monkeypatch.setattr(hermes, "chat", boom)

    sid = client.post("/sessions", json={"type": "chat"}).json()["id"]
    resp = client.post(f"/sessions/{sid}/chat", json={"message": "hello"})
    assert resp.status_code == 502
    # a failed turn must leave no half-written transcript behind
    assert client.get(f"/sessions/{sid}").json()["payload"] is None
