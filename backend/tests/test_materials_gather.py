"""The gather graph: local inbox + Drive discovery, auto-import, no approval.

Hermes and the embedding model are stubbed, same as test_materials.py -- SQLite
and the filesystem inbox are real, since the whole point of these tests is
checking what actually moves where.
"""

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["UPLOADS_PATH"] = str(_TMP / "uploads")
os.environ["MATERIALS_INBOX_PATH"] = str(_TMP / "materials_inbox")
os.environ["MATERIALS_GATHER_TOKEN"] = "test-gather-token"
os.environ["WARM_EMBEDDINGS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.connections import google_drive  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import repository as repo  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402
from app.materials.gather import repository as gather_repo  # noqa: E402

HEADERS = {"X-Gather-Token": "test-gather-token"}

# Matches "[3] some name.pdf (text, 120 bytes)" out of relevance._prompt's listing.
_CANDIDATE_RE = re.compile(r"\[(\d+)\] (.*?) \(")


def _fake_vector(text: str) -> list[float]:
    dim = get_settings().embedding_dim
    return [0.0] * dim


@pytest.fixture(scope="module", autouse=True)
def stubs():
    """`hermes.complete` has to answer two different prompt shapes: the
    gather graph's relevance decision and the ingest pipeline's tagger, since
    an accepted candidate runs the full ingest pipeline via BackgroundTasks."""

    async def fake_complete(prompt, *, system=None):
        if "Candidate files found" in prompt:
            selected = [
                int(i)
                for i, name in _CANDIDATE_RE.findall(prompt)
                if "irrelevant" not in name.lower()
            ]
            return json.dumps({"selected": selected})
        # Tagger prompt: one shared topic is enough, these tests don't assert
        # on topic names.
        assignments = [
            {"index": int(i), "topic_name": "Gathered", "topic_description": "x"}
            for i in re.findall(r"\[(\d+)\] ", prompt)
        ]
        return json.dumps({"assignments": assignments})

    original = (hermes.complete, embeddings.embed_passages, embeddings.embed_query)
    hermes.complete = fake_complete
    embeddings.embed_passages = lambda texts: [_fake_vector(t) for t in texts]
    embeddings.embed_query = _fake_vector
    yield
    hermes.complete, embeddings.embed_passages, embeddings.embed_query = original


@pytest.fixture(scope="module")
def client(stubs):
    init_db()
    vector_store.drop()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_inbox():
    """Function-scoped: each test starts with an empty inbox regardless of
    what a previous test left behind."""
    inbox = get_settings().materials_inbox_path
    inbox.mkdir(parents=True, exist_ok=True)
    for child in inbox.iterdir():
        if child.is_file():
            child.unlink()
    yield


@pytest.fixture(autouse=True)
def no_drive(monkeypatch):
    """Default: Drive is not connected, matching a fresh test DB with no
    `connections` row. Tests that need Drive results override this."""

    async def not_connected():
        raise google_drive.DriveNotConnected("not connected in this test")

    monkeypatch.setattr(google_drive, "access_token", not_connected)


def _drop_inbox_file(
    name: str, text: str = "Some study notes about entropy and the second law of thermodynamics."
) -> Path:
    path = get_settings().materials_inbox_path / name
    path.write_text(text)
    return path


def _run_gather(client, headers=HEADERS) -> dict:
    resp = client.post("/materials/gather/run", headers=headers)
    return resp


class TestAuth:
    def test_missing_token_is_rejected(self, client):
        resp = client.post("/materials/gather/run")
        assert resp.status_code == 403

    def test_correct_token_is_accepted(self, client):
        resp = _run_gather(client)
        assert resp.status_code == 200


class TestLocalInbox:
    def test_accepted_candidate_is_imported_and_removed_from_inbox(self, client):
        path = _drop_inbox_file("lecture notes.txt")

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["imported_file_ids"]) == 1
        assert not path.exists()

        source_file = client.get(
            f"/materials/uploads/{body['imported_file_ids'][0]}"
        ).json()
        assert source_file["ingest_status"] == "ready", source_file["ingest_error"]
        assert source_file["origin"] == "local"

    def test_rejected_candidate_is_moved_to_skipped_not_deleted(self, client):
        path = _drop_inbox_file("irrelevant scan.txt", "an irrelevant personal file")

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported_file_ids"] == []
        assert body["skipped_local"] == ["irrelevant scan.txt"]
        assert not path.exists()

        skipped = get_settings().materials_inbox_path / ".skipped" / "irrelevant scan.txt"
        assert skipped.exists()

        with connection() as conn:
            rows = conn.execute(
                "SELECT * FROM source_files WHERE filename = 'irrelevant scan.txt'"
            ).fetchall()
        assert rows == []

    def test_unclassifiable_file_never_reaches_hermes_and_is_skipped(self, client):
        calls_before = []
        original = hermes.complete

        async def counting(prompt, *, system=None):
            calls_before.append(prompt)
            return await original(prompt, system=system)

        hermes.complete = counting
        try:
            _drop_inbox_file("mystery.xyz", "unrecognised extension")
            resp = _run_gather(client)
        finally:
            hermes.complete = original

        assert resp.status_code == 200
        body = resp.json()
        assert body["imported_file_ids"] == []
        assert body["skipped_local"] == ["mystery.xyz"]
        # No candidates worth judging -> decide_relevance short-circuits.
        assert calls_before == []

    def test_empty_inbox_calls_hermes_zero_times(self, client):
        calls = []
        original = hermes.complete

        async def counting(prompt, *, system=None):
            calls.append(prompt)
            return await original(prompt, system=system)

        hermes.complete = counting
        try:
            resp = _run_gather(client)
        finally:
            hermes.complete = original

        assert resp.status_code == 200
        assert resp.json()["candidates_seen"] == 0
        assert calls == []


class TestCapAndDegradation:
    def test_over_selection_is_truncated_to_the_cap(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "materials_gather_max_imports", 1)
        _drop_inbox_file("first.txt", "first set of notes")
        _drop_inbox_file("second.txt", "second set of notes")

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["imported_file_ids"]) == 1
        # The one over the cap was never selected, so it was rejected same as
        # any other non-pick.
        assert len(body["skipped_local"]) == 1

    def test_llm_unavailable_imports_nothing_and_records_the_error(self, client):
        async def down(prompt, *, system=None):
            raise hermes.HermesError("gateway unreachable")

        original = hermes.complete
        hermes.complete = down
        try:
            _drop_inbox_file("unjudged.txt")
            resp = _run_gather(client)
        finally:
            hermes.complete = original

        assert resp.status_code == 200
        body = resp.json()
        assert body["imported_file_ids"] == []

        with connection() as conn:
            row = conn.execute(
                "SELECT * FROM materials_gather_runs WHERE id = ?", (body["run_id"],)
            ).fetchone()
        assert row["error"] is not None
        # The file was judged but not placed anywhere -- it must still exist
        # somewhere, not vanish. Since it was never selected, it's skipped.
        assert body["skipped_local"] == ["unjudged.txt"]


class TestDrive:
    FILE_META = {
        "id": "drive-known-1",
        "name": "already imported.pdf",
        "mimeType": "text/plain",
        "modifiedTime": "2026-09-20T00:00:00Z",
        "webViewLink": "https://drive.google.com/file/d/drive-known-1/view",
    }
    NEW_META = {
        "id": "drive-new-1",
        "name": "new lecture.txt",
        "mimeType": "text/plain",
        "modifiedTime": "2026-09-22T00:00:00Z",
        "webViewLink": "https://drive.google.com/file/d/drive-new-1/view",
    }

    def _connect_drive(self, monkeypatch, files, *, page_size_cap=None):
        async def fake_token():
            return "fake-token"

        async def fake_list_files(token, *, query, page_token=None, page_size=50):
            return {"files": files, "nextPageToken": None}

        monkeypatch.setattr(google_drive, "access_token", fake_token)
        monkeypatch.setattr(google_drive, "list_files", fake_list_files)

    def test_already_known_drive_file_is_refreshed_without_relevance_check(
        self, client, monkeypatch
    ):
        with connection() as conn:
            row = repo.create_source_file(
                conn,
                filename="stale name.pdf",
                upload_type="text",
                origin="drive",
                drive_file_id=self.FILE_META["id"],
                drive_url="https://old-link",
                drive_modified_at="2026-01-01T00:00:00Z",
            )

        self._connect_drive(monkeypatch, [self.FILE_META])

        calls = []
        original = hermes.complete

        async def counting(prompt, *, system=None):
            calls.append(prompt)
            return await original(prompt, system=system)

        hermes.complete = counting
        try:
            resp = _run_gather(client)
        finally:
            hermes.complete = original

        assert resp.status_code == 200
        body = resp.json()
        assert row["id"] in body["refreshed_file_ids"]
        assert not any("Candidate files found" in c for c in calls)

        with connection() as conn:
            refreshed = repo.get_source_file(conn, row["id"])
        assert refreshed["filename"] == self.FILE_META["name"]
        assert refreshed["drive_url"] == self.FILE_META["webViewLink"]

    def test_new_drive_file_is_judged_and_can_be_imported(self, client, monkeypatch):
        self._connect_drive(monkeypatch, [self.NEW_META])

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["imported_file_ids"]) == 1

        with connection() as conn:
            row = repo.get_source_file(conn, body["imported_file_ids"][0])
        assert row["origin"] == "drive"
        assert row["drive_file_id"] == self.NEW_META["id"]

    def test_scan_cap_hit_withholds_the_cursor(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "materials_gather_drive_scan_cap", 1)
        many = [
            {**self.NEW_META, "id": f"drive-cap-{i}", "name": f"cap file {i}.txt"}
            for i in range(3)
        ]
        self._connect_drive(monkeypatch, many)

        with connection() as conn:
            before_cursor = gather_repo.start_run(conn)["drive_cursor"]
            conn.execute(
                "DELETE FROM materials_gather_runs WHERE drive_cursor IS NULL AND finished_at IS NULL"
            )

        resp = _run_gather(client)
        assert resp.status_code == 200

        with connection() as conn:
            after_cursor = gather_repo.start_run(conn)["drive_cursor"]
        assert after_cursor == before_cursor
