"""POST /system/reset -- the whole-app "start from scratch" button.

One long test walks the whole app: a real upload (real chunking, real vector
row), a chat session, and a goal/roadmap-run/quiz-creation-run/gather-run chain
seeded directly in SQL -- those graphs already have their own end-to-end tests
(test_goal_graph.py, test_quiz_create_graph.py, test_materials_gather.py); what
matters here is that the reset's table coverage is complete. A real Google
grant is connected end to end so the revoke + Hermes-file-delete path in the
reset endpoint is exercised for real, same as test_connections.py's disconnect
tests.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["UPLOADS_PATH"] = str(_TMP / "uploads")
os.environ["HERMES_CONFIG_PATH"] = str(_TMP / "hermes")
os.environ["HERMES_FILES_URL"] = ""
os.environ["CONNECTIONS_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["WARM_EMBEDDINGS"] = "false"

from app.config import get_settings  # noqa: E402

# get_settings() is process-wide @lru_cache'd -- see test_materials_gather.py's
# identical comment for why this has to run before app.main is imported.
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
from app.connections import google_oauth, hermes_files  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402

CLIENT_JSON = {
    "installed": {
        "client_id": "123.apps.googleusercontent.com",
        "project_id": "athena-test",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "GOCSPX-testsecret",
        "redirect_uris": ["http://localhost"],
    }
}

TOKEN_RESPONSE = {
    "access_token": "ya29.test-access",
    "refresh_token": "1//test-refresh",
    "expires_in": 3599,
    "scope": (
        "https://www.googleapis.com/auth/drive.readonly "
        "https://www.googleapis.com/auth/userinfo.email"
    ),
    "token_type": "Bearer",
}

TABLES = [
    "source_files",
    "topics",
    "chunks",
    "sessions",
    "quiz_attempts",
    "goals",
    "milestones",
    "quizzes",
    "quiz_questions",
    "understanding_events",
    "roadmap_runs",
    "quiz_creation_runs",
    "materials_gather_runs",
    "settings",
]


@pytest.fixture(scope="module", autouse=True)
def stubs():
    """One shared topic is enough -- this file doesn't assert on tagging."""

    async def fake_complete(prompt, *, system=None):
        return json.dumps(
            {"assignments": [{"index": 0, "topic_name": "Reset", "topic_description": "x"}]}
        )

    dim = get_settings().embedding_dim
    original = (hermes.complete, embeddings.embed_passages, embeddings.embed_query)
    hermes.complete = fake_complete
    embeddings.embed_passages = lambda texts: [[0.0] * dim for _ in texts]
    embeddings.embed_query = lambda text: [0.0] * dim
    yield
    hermes.complete, embeddings.embed_passages, embeddings.embed_query = original


@pytest.fixture(scope="module")
def client(stubs):
    init_db()
    vector_store.drop()
    with TestClient(app) as c:
        yield c


def test_reset_wipes_the_entire_app(client, monkeypatch):
    # Materials: a real upload, tagged and embedded via the stubs above.
    upload_resp = client.post(
        "/materials/uploads/text",
        json={
            "filename": "reset.txt",
            "text": "Notes long enough to chunk for the reset test. " * 20,
        },
    )
    assert upload_resp.status_code == 202
    assert client.get("/materials/topics").json()
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] > 0

    # A chat session.
    assert client.post("/sessions", json={"type": "chat"}).status_code == 201

    # Goal / roadmap-run / quiz-creation-run / gather-run / a settings value --
    # seeded directly, since driving each graph end to end is somebody else's
    # test file.
    with connection() as conn:
        goal = conn.execute(
            "INSERT INTO goals (title) VALUES ('Ace thermo') RETURNING id"
        ).fetchone()
        conn.execute(
            "INSERT INTO milestones (goal_id, title, order_index) VALUES (?, 'Step 1', 0)",
            (goal["id"],),
        )
        conn.execute(
            "INSERT INTO roadmap_runs (thread_id, status, raw_goal_input) "
            "VALUES ('thread-1', 'clarifying', 'ace thermo')"
        )
        conn.execute(
            "INSERT INTO quiz_creation_runs (thread_id, status) VALUES ('qc-1', 'choosing_topic')"
        )
        conn.execute("INSERT INTO materials_gather_runs DEFAULT VALUES")
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('materials.gather_interval', '\"weekly\"')"
        )

    # A connected Google grant, end to end.
    upload_client = client.post(
        "/connections/google/client",
        files={"file": ("client.json", json.dumps(CLIENT_JSON).encode(), "application/json")},
    )
    assert upload_client.status_code == 202

    async def fake_exchange(client_conf, code):
        return TOKEN_RESPONSE

    async def fake_email(access_token):
        return "student@university.edu"

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(google_oauth, "fetch_account_email", fake_email)
    exchange_resp = client.post("/connections/google/exchange", json={"pasted": "4/0Atest"})
    assert exchange_resp.status_code == 200
    assert exchange_resp.json()["status"] == "connected"

    hermes_dir = Path(os.environ["HERMES_CONFIG_PATH"])
    assert (hermes_dir / hermes_files.GOOGLE_TOKEN_FILE).exists()

    revoked = {}

    async def fake_revoke(refresh_token):
        revoked["token"] = refresh_token
        return None

    monkeypatch.setattr(google_oauth, "revoke", fake_revoke)

    resp = client.post("/system/reset")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"warning": None}
    assert revoked["token"] == TOKEN_RESPONSE["refresh_token"]

    assert not (hermes_dir / hermes_files.GOOGLE_TOKEN_FILE).exists()
    assert not (hermes_dir / hermes_files.GOOGLE_CLIENT_FILE).exists()

    with connection() as conn:
        for table in TABLES:
            assert conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"] == 0, table
        rows = conn.execute("SELECT status, secret FROM connections").fetchall()
        assert rows and all(r["status"] == "disconnected" and r["secret"] is None for r in rows)
        assert conn.execute("SELECT COUNT(*) c FROM connection_capabilities").fetchone()["c"] == 0

    assert vector_store.search([0.0] * get_settings().embedding_dim) == []
    assert list(Path(get_settings().uploads_path).iterdir()) == []

    assert client.get("/materials/topics").json() == []
    assert client.get("/sessions").json()["items"] == []


def test_reset_surfaces_a_revoke_failure_without_blocking(client, monkeypatch):
    """The reset must still finish -- and clear everything -- even when
    revoking Google fails, same contract as DELETE /connections/google."""
    client.post(
        "/connections/google/client",
        files={"file": ("client.json", json.dumps(CLIENT_JSON).encode(), "application/json")},
    )

    async def fake_exchange(client_conf, code):
        return TOKEN_RESPONSE

    async def fake_email(access_token):
        return "student@university.edu"

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(google_oauth, "fetch_account_email", fake_email)
    client.post("/connections/google/exchange", json={"pasted": "4/0Atest"})

    async def failing_revoke(refresh_token):
        return "could not reach Google to revoke the token: boom"

    monkeypatch.setattr(google_oauth, "revoke", failing_revoke)

    resp = client.post("/system/reset")
    assert resp.status_code == 200, resp.text
    assert "revoke" in resp.json()["warning"]

    with connection() as conn:
        row = conn.execute("SELECT status, secret FROM connections WHERE slug = 'google'").fetchone()
        assert row["status"] == "disconnected"
        assert row["secret"] is None
