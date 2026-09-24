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
# Explicit, not just "unset": a developer's local backend/.env may well have
# TEST_MODE=true for their own use of the reset button, and this file must
# not inherit that -- TestTestModeReset flips it on per-test, deliberately.
os.environ["TEST_MODE"] = "false"

from app.config import get_settings  # noqa: E402

# get_settings() is process-wide @lru_cache'd, and another test module
# collected before this one (test_materials.py, alphabetically) may already
# have imported app.main and cached a Settings built from backend/.env's real
# values -- notably a real MATERIALS_GATHER_TOKEN, which would make every
# X-Gather-Token check in this file fail against a token it never set.
# Clearing here, before app.main is (re-)imported below, forces the next
# get_settings() call to read the environment as this file has just set it.
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
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


FOLDER_META = {
    "id": "folder-abc123",
    "name": "CHEM 2010",
    "mimeType": "application/vnd.google-apps.folder",
}


class TestFolderConfig:
    def test_default_is_no_restriction(self, client):
        resp = client.get("/materials/gather/config")
        assert resp.status_code == 200
        assert resp.json() == {"folder_id": None, "folder_name": None}

    def test_set_folder_resolves_against_drive_and_persists(self, client, monkeypatch):
        async def fake_token():
            return "fake-token"

        async def fake_get_file(token, file_id):
            assert file_id == FOLDER_META["id"]
            return FOLDER_META

        monkeypatch.setattr(google_drive, "access_token", fake_token)
        monkeypatch.setattr(google_drive, "get_file", fake_get_file)

        resp = client.put(
            "/materials/gather/config",
            json={"folder": f"https://drive.google.com/drive/folders/{FOLDER_META['id']}?usp=sharing"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"folder_id": FOLDER_META["id"], "folder_name": "CHEM 2010"}

        # Persisted -- a fresh GET sees it too, not just the PUT's own response.
        assert client.get("/materials/gather/config").json()["folder_id"] == FOLDER_META["id"]

        resp = client.delete("/materials/gather/config")
        assert resp.status_code == 200
        assert resp.json() == {"folder_id": None, "folder_name": None}

    def test_non_folder_is_rejected(self, client, monkeypatch):
        async def fake_token():
            return "fake-token"

        async def fake_get_file(token, file_id):
            return {"id": file_id, "name": "not a folder.txt", "mimeType": "text/plain"}

        monkeypatch.setattr(google_drive, "access_token", fake_token)
        monkeypatch.setattr(google_drive, "get_file", fake_get_file)

        resp = client.put("/materials/gather/config", json={"folder": "not-a-folder-id"})
        assert resp.status_code == 400
        assert "not a Drive folder" in resp.json()["detail"]

    def test_garbage_input_is_rejected_before_any_drive_call(self, client, monkeypatch):
        calls = []

        async def counting_token():
            calls.append(1)
            return "fake-token"

        monkeypatch.setattr(google_drive, "access_token", counting_token)

        resp = client.put(
            "/materials/gather/config", json={"folder": "https://example.com/not/a/drive/link"}
        )
        assert resp.status_code == 400
        assert calls == []

    def test_drive_not_connected_gives_409(self, client):
        # `no_drive` (autouse) leaves Drive disconnected by default.
        resp = client.put(
            "/materials/gather/config",
            json={"folder": f"https://drive.google.com/drive/folders/{FOLDER_META['id']}"},
        )
        assert resp.status_code == 409


class TestFolderScopesDriveQuery:
    def test_configured_folder_narrows_the_drive_query(self, client, monkeypatch):
        with connection() as conn:
            gather_repo.set_drive_folder(
                conn, folder_id=FOLDER_META["id"], folder_name=FOLDER_META["name"]
            )

        seen_queries = []

        async def fake_token():
            return "fake-token"

        async def fake_list_files(token, *, query, page_token=None, page_size=50):
            seen_queries.append(query)
            return {"files": [], "nextPageToken": None}

        monkeypatch.setattr(google_drive, "access_token", fake_token)
        monkeypatch.setattr(google_drive, "list_files", fake_list_files)

        resp = _run_gather(client)
        assert resp.status_code == 200
        assert seen_queries
        assert f"'{FOLDER_META['id']}' in parents" in seen_queries[0]

        with connection() as conn:
            gather_repo.clear_drive_folder(conn)


_PARENT_RE = re.compile(r"'([^']+)' in parents")


class TestFolderRecursion:
    """Drive's API has no recursive folder search -- scan_drive.py walks the
    tree itself. Each test uses its own file/folder ids, distinct from every
    other test in this file: `source_ids_by_drive_id` dedup is global to the
    shared test DB, and a ready-made file id created above the class it does
    not belong to would be misclassified as drive_refresh here."""

    def _connect(self, monkeypatch, *, tree, files, folder_cap=None):
        if folder_cap is not None:
            monkeypatch.setattr(get_settings(), "materials_gather_drive_folder_cap", folder_cap)

        async def fake_token():
            return "fake-token"

        async def fake_list_files(token, *, query, page_token=None, page_size=50):
            match = _PARENT_RE.search(query)
            parent_id = match.group(1) if match else None
            if "vnd.google-apps.folder" in query:
                children = [
                    {"id": cid, "name": cid, "mimeType": "application/vnd.google-apps.folder"}
                    for cid in tree.get(parent_id, [])
                ]
                return {"files": children, "nextPageToken": None}
            return {"files": files.get(parent_id, []), "nextPageToken": None}

        monkeypatch.setattr(google_drive, "access_token", fake_token)
        monkeypatch.setattr(google_drive, "list_files", fake_list_files)

    def test_files_in_a_subfolder_are_found(self, client, monkeypatch):
        tree = {"root-a": ["sub-a"], "sub-a": []}
        files = {
            "root-a": [
                {
                    "id": "direct-a",
                    "name": "direct.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-09-22T00:00:00Z",
                }
            ],
            "sub-a": [
                {
                    "id": "nested-a",
                    "name": "nested.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-09-22T00:00:00Z",
                }
            ],
        }

        with connection() as conn:
            gather_repo.set_drive_folder(conn, folder_id="root-a", folder_name="Root")
        self._connect(monkeypatch, tree=tree, files=files)

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["imported_file_ids"]) == 2

        with connection() as conn:
            names = {
                repo.get_source_file(conn, fid)["filename"] for fid in body["imported_file_ids"]
            }
            gather_repo.clear_drive_folder(conn)
        assert names == {"direct.txt", "nested.txt"}

    def test_folder_cap_stops_the_walk_before_the_subfolder(self, client, monkeypatch):
        tree = {"root-b": ["sub-b"], "sub-b": []}
        files = {
            "root-b": [
                {
                    "id": "direct-b",
                    "name": "direct.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-09-22T00:00:00Z",
                }
            ],
            "sub-b": [
                {
                    "id": "nested-b",
                    "name": "nested.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2026-09-22T00:00:00Z",
                }
            ],
        }

        with connection() as conn:
            gather_repo.set_drive_folder(conn, folder_id="root-b", folder_name="Root")
        self._connect(monkeypatch, tree=tree, files=files, folder_cap=1)

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()

        with connection() as conn:
            names = {
                repo.get_source_file(conn, fid)["filename"] for fid in body["imported_file_ids"]
            }
            gather_repo.clear_drive_folder(conn)
        # Only root-b itself was visited -- direct.txt is found, nested.txt
        # (inside sub-b, which the cap never reached) is not.
        assert names == {"direct.txt"}


class TestSessionLogging:
    def test_run_writes_a_findable_knowledge_sync_session(self, client):
        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["candidates_seen"] == 0

        session = client.get(f"/sessions/{body['session_id']}").json()
        assert session["type"] == "cron"
        assert session["title"] == "Materials gather"
        # Zero candidates is the common case on a healthy schedule and must
        # not read as "0 of 0", which looks broken rather than idle.
        assert session["summary"] == "Nothing new since the last sync"

        listed = client.get("/sessions?type=cron").json()["items"]
        assert any(s["id"] == body["session_id"] for s in listed)

    def test_a_run_with_candidates_gets_an_itemised_summary(self, client):
        _drop_inbox_file("summary check.txt")

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["candidates_seen"] > 0

        session = client.get(f"/sessions/{body['session_id']}").json()
        assert str(body["candidates_seen"]) in session["summary"]
        assert "imported" in session["summary"]


class TestRunMaterials:
    """GET /materials/gather/runs/{session_id} -- the topics-acquired view."""

    def test_topics_and_files_for_an_imported_file(self, client):
        _drop_inbox_file(
            "chem notes.txt",
            "Some study notes about entropy and the second law of thermodynamics.",
        )

        resp = _run_gather(client)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["imported_file_ids"]) == 1

        detail = client.get(f"/materials/gather/runs/{body['session_id']}").json()
        assert detail["session_id"] == body["session_id"]
        assert detail["skipped"] == []
        assert detail["pending"] == []
        assert detail["removed"] == []

        # `stubs`' tagger fixture assigns every chunk to a single "Gathered"
        # topic -- one topic, one file, at least one chunk.
        assert len(detail["topics"]) == 1
        topic = detail["topics"][0]
        assert topic["topic_name"] == "Gathered"
        assert len(topic["files"]) == 1

        file_row = topic["files"][0]
        assert file_row["source_file_id"] == body["imported_file_ids"][0]
        assert file_row["filename"] == "chem notes.txt"
        assert file_row["origin"] == "local"
        assert file_row["chunk_count"] >= 1

    def test_skipped_files_are_listed_but_never_become_a_topic(self, client):
        _drop_inbox_file("irrelevant memo.txt", "an irrelevant personal file")

        resp = _run_gather(client)
        body = resp.json()
        assert body["imported_file_ids"] == []

        detail = client.get(f"/materials/gather/runs/{body['session_id']}").json()
        assert detail["topics"] == []
        assert detail["pending"] == []
        assert detail["removed"] == []
        assert detail["skipped"] == ["irrelevant memo.txt"]

    def test_a_file_deleted_after_the_run_shows_up_as_removed_not_vanished(self, client):
        """A run's own summary line ("1 imported ...") must never contradict
        its detail view -- deleting the file afterward (an ordinary action on
        the Materials page) must not make the run look like it added nothing."""
        _drop_inbox_file(
            "will be deleted.txt",
            "Notes on reaction kinetics and rate laws for the exam.",
        )

        resp = _run_gather(client)
        body = resp.json()
        assert len(body["imported_file_ids"]) == 1

        assert client.delete(f"/materials/uploads/{body['imported_file_ids'][0]}").status_code == 204

        detail = client.get(f"/materials/gather/runs/{body['session_id']}").json()
        assert detail["topics"] == []
        assert detail["pending"] == []
        assert detail["removed"] == ["will be deleted.txt"]

    def test_unknown_session_is_404(self, client):
        assert client.get("/materials/gather/runs/999999").status_code == 404

    def test_a_non_gather_cron_session_is_404(self, client):
        """The discriminator is `payload.kind`, not just `type == 'cron'` --
        a future Hermes-driven finding sharing the type must not be openable
        through this endpoint, which assumes gather's payload shape."""
        with connection() as conn:
            row = conn.execute(
                "INSERT INTO sessions (type, summary) VALUES ('cron', 'unrelated finding') "
                "RETURNING id"
            ).fetchone()

        resp = client.get(f"/materials/gather/runs/{row['id']}")
        assert resp.status_code == 404


class TestTestModeReset:
    """GET /materials/gather/test-mode, POST /materials/gather/reset.
    TEST_MODE is off by default in this file's environment (not set above),
    so the "off" behaviour is exercised first, then flipped on per-test via
    monkeypatch -- never left on for a test it didn't ask for."""

    def test_test_mode_is_off_by_default(self, client):
        assert client.get("/materials/gather/test-mode").json() == {"enabled": False}

    def test_reset_is_404_outside_test_mode(self, client):
        resp = client.post("/materials/gather/reset")
        assert resp.status_code == 404

    def test_reset_wipes_materials_and_gather_state_but_not_other_sessions(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(get_settings(), "test_mode", True)
        assert client.get("/materials/gather/test-mode").json() == {"enabled": True}

        # Materials: a real, tagged upload.
        _upload = client.post(
            "/materials/uploads/text",
            json={
                "filename": "reset check.txt",
                "text": "Notes on reaction kinetics for the reset test, long enough to chunk.",
            },
        )
        assert _upload.status_code == 202
        assert client.get("/materials/topics").json()

        # Gather state: a folder scope and a run log entry.
        with connection() as conn:
            gather_repo.set_drive_folder(conn, folder_id="folder-x", folder_name="Folder X")
            gather_repo.start_run(conn)

        # A gather-produced session, and an unrelated cron session that must
        # survive -- the reset is scoped to what gather itself produced, not
        # every row sharing its `type`.
        gather_resp = _run_gather(client)
        assert gather_resp.status_code == 200
        with connection() as conn:
            unrelated = conn.execute(
                "INSERT INTO sessions (type, summary) VALUES ('cron', 'not from gather') "
                "RETURNING id"
            ).fetchone()

        resp = client.post("/materials/gather/reset")
        assert resp.status_code == 204

        assert client.get("/materials/topics").json() == []
        assert client.get("/materials/uploads").json() == []
        assert client.get("/materials/gather/config").json() == {
            "folder_id": None,
            "folder_name": None,
        }
        assert client.get(f"/materials/gather/runs/{gather_resp.json()['session_id']}").status_code == 404

        with connection() as conn:
            assert conn.execute("SELECT COUNT(*) c FROM materials_gather_runs").fetchone()["c"] == 0
            assert conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] == 0

        # The unrelated session was never gather's to delete.
        assert client.get(f"/sessions/{unrelated['id']}").status_code == 200
