"""The Google Drive bridge: picker, import, and fetching during ingest.

Google is stubbed at the httpx boundary; SQLite, Fernet and the pipeline are
real. The thing worth testing is that a Drive file becomes a pointer row with
no bytes on this machine and still ends up chunked and tagged -- faking the
pipeline would leave exactly that unverified.

Tagging and embedding are stubbed because they are covered in test_materials.py
and cost a 130MB download here.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["UPLOADS_PATH"] = str(_TMP / "uploads")
os.environ["WARM_EMBEDDINGS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.connections import crypto, google_drive  # noqa: E402
from app.connections import repository as conn_repo  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import drive  # noqa: E402
from app.materials import repository as repo  # noqa: E402
from app.materials.ingest import pipeline  # noqa: E402

TEST_KEY = Fernet.generate_key().decode()

CLIENT = {
    "client_id": "123.apps.googleusercontent.com",
    "client_secret": "GOCSPX-testsecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# Long enough to survive the chunker and be recognisably about one topic.
LECTURE_TEXT = (
    "Entropy is the measure of disorder in a thermodynamic system. "
    "The second law states that the entropy of an isolated system never "
    "decreases over time, which is why reactions run the direction they do. "
) * 6


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "athena.db"))
    monkeypatch.setenv("LANCEDB_PATH", str(tmp_path / "lancedb"))
    monkeypatch.setenv("UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("CONNECTIONS_SECRET_KEY", TEST_KEY)
    monkeypatch.setenv("WARM_EMBEDDINGS", "false")
    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    init_db()
    yield
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


@pytest.fixture
def client():
    return TestClient(app)


def connect_google(*, expires_in=timedelta(hours=1), drive_read=True, token=None):
    """Put a connected Google row in the DB, the way /exchange would have."""
    with connection() as conn:
        conn_repo.upsert_authorizing(
            conn,
            slug=conn_repo.GOOGLE_SLUG,
            provider="google",
            display_name="Google",
            secret=CLIENT,
        )
        row = conn_repo.mark_connected(
            conn,
            slug=conn_repo.GOOGLE_SLUG,
            secret={
                **CLIENT,
                "token": token
                or {"access_token": "ya29.test", "refresh_token": "1//refresh"},
            },
            account_label="zhong@university.edu",
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            expires_at=_iso(expires_in),
        )
        if drive_read:
            conn_repo.grant_capability(conn, row["id"], "drive.read")
    return row


def stub_drive(
    monkeypatch, *, files=None, meta=None, content=b"", token="ya29.test"
):
    """Replace the three Drive calls. Returns a log of what was asked for."""
    calls = {"list": [], "get": [], "download": []}

    async def fake_token():
        return token

    async def fake_list(tok, *, query, page_token=None, page_size=50):
        calls["list"].append({"query": query, "page_token": page_token})
        return files or {"files": []}

    async def fake_get(tok, file_id):
        calls["get"].append(file_id)
        if meta is None:
            raise google_drive.DriveError("no metadata stubbed")
        return meta[file_id] if isinstance(meta, dict) and file_id in meta else meta

    async def fake_download(tok, file_id, *, export_mime, limit):
        calls["download"].append({"id": file_id, "export_mime": export_mime})
        return content

    monkeypatch.setattr(google_drive, "access_token", fake_token)
    monkeypatch.setattr(google_drive, "list_files", fake_list)
    monkeypatch.setattr(google_drive, "get_file", fake_get)
    monkeypatch.setattr(google_drive, "download", fake_download)
    return calls


def stub_pipeline_llm(monkeypatch):
    """Skip tagging and embedding: both are covered in test_materials.py."""

    async def no_tagging(file_id, chunk_ids, texts):
        return None

    async def no_embedding(file_id):
        return None

    monkeypatch.setattr(pipeline, "_tag", no_tagging)
    monkeypatch.setattr(pipeline, "_embed", no_embedding)


# --------------------------------------------------------------------------
# mime policy
# --------------------------------------------------------------------------


def test_native_formats_are_exported_not_downloaded():
    # A Google Doc has no bytes of its own; fetching it with alt=media returns
    # a 403, so the plan has to name an export format.
    assert drive.plan_for("application/vnd.google-apps.document") == (
        "text",
        "text/plain",
    )
    # Sheets export as CSV -- text/plain would flatten the columns away.
    assert drive.plan_for("application/vnd.google-apps.spreadsheet") == (
        "text",
        "text/csv",
    )


def test_binary_formats_are_downloaded_as_is():
    assert drive.plan_for("application/pdf") == ("pdf", None)
    assert drive.plan_for("image/png") == ("image", None)


def test_unsupported_mime_has_no_plan():
    assert drive.plan_for("application/zip") is None
    assert drive.plan_for(drive.FOLDER_MIME) is None


def test_every_supported_mime_maps_to_an_extractable_type():
    """The mapping in drive.py and the extractor must not drift apart: a mime
    that imports cleanly and then cannot be extracted fails in the background,
    minutes later, where the user cannot see why."""
    from app.materials.ingest.extract import UPLOAD_TYPES

    for mime in drive.SUPPORTED_MIMES:
        upload_type, _ = drive.plan_for(mime)
        assert upload_type in UPLOAD_TYPES, mime


def test_search_terms_cannot_break_out_of_the_drive_query():
    """Drive's `q` is a string grammar with single-quoted literals, so an
    apostrophe is a syntax error unless escaped -- and an unescaped one is a
    query injection."""
    query = drive.build_query("Fermat's theorem")
    assert "name contains 'Fermat\\'s theorem'" in query

    backslash = drive.build_query("a\\b")
    assert "name contains 'a\\\\b'" in backslash


def test_query_asks_only_for_ingestible_files():
    query = drive.build_query()
    assert "trashed = false" in query
    assert drive.FOLDER_MIME not in query
    assert "application/pdf" in query


# --------------------------------------------------------------------------
# access: connection state and token refresh
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_access_refused_when_google_is_not_connected():
    with pytest.raises(google_drive.DriveNotConnected, match="not connected"):
        await google_drive.access_token()


@pytest.mark.anyio
async def test_access_refused_when_drive_read_is_switched_off():
    """A granted scope is not a standing permission: the Settings toggle has to
    actually gate reading, or it lies about what Αθηνα can do."""
    connect_google(drive_read=False)
    with pytest.raises(google_drive.DriveNotConnected, match="switched off"):
        await google_drive.access_token()


@pytest.mark.anyio
async def test_unexpired_token_is_reused_without_calling_google(monkeypatch):
    connect_google(expires_in=timedelta(hours=1))

    async def explode(*a, **k):
        raise AssertionError("refreshed a token that had not expired")

    monkeypatch.setattr(google_drive, "_refresh", explode)
    assert await google_drive.access_token() == "ya29.test"


@pytest.mark.anyio
async def test_expired_token_is_refreshed_and_persisted(monkeypatch):
    connect_google(expires_in=timedelta(minutes=-5))

    async def fake_refresh(client_conf, refresh_token):
        assert refresh_token == "1//refresh"
        return {"access_token": "ya29.fresh", "expires_in": 3600}

    monkeypatch.setattr(google_drive, "_refresh", fake_refresh)
    assert await google_drive.access_token() == "ya29.fresh"

    # Persisted, so the next request does not refresh again.
    with connection() as conn:
        stored = conn_repo.get_credentials(conn, conn_repo.GOOGLE_SLUG)
        row = conn_repo.get(conn, conn_repo.GOOGLE_SLUG)
    assert stored["token"]["access_token"] == "ya29.fresh"
    # Google omits refresh_token from a refresh response; losing it here would
    # break every later refresh.
    assert stored["token"]["refresh_token"] == "1//refresh"
    assert row["expires_at"] > _iso(timedelta(minutes=50))


@pytest.mark.anyio
async def test_token_expiring_within_the_skew_is_refreshed_early(monkeypatch):
    """A token that dies mid-ingest costs a Drive round-trip that already
    happened, so it is refreshed before it strictly has to be."""
    connect_google(expires_in=timedelta(minutes=2))
    called = []

    async def fake_refresh(client_conf, refresh_token):
        called.append(True)
        return {"access_token": "ya29.fresh", "expires_in": 3600}

    monkeypatch.setattr(google_drive, "_refresh", fake_refresh)
    await google_drive.access_token()
    assert called


@pytest.mark.anyio
async def test_revoked_grant_asks_the_user_to_reconnect(monkeypatch):
    """invalid_grant is unrecoverable without user action, so it must not
    surface as a generic upstream error the UI would tell them to retry."""
    import httpx

    connect_google(expires_in=timedelta(minutes=-5))

    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            400, json={"error": "invalid_grant"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(google_drive.DriveNotConnected, match="Reconnect"):
        await google_drive.access_token()


# --------------------------------------------------------------------------
# the picker
# --------------------------------------------------------------------------


def test_picker_lists_supported_files_and_drops_the_rest(client, monkeypatch):
    connect_google()
    stub_drive(
        monkeypatch,
        files={
            "files": [
                {
                    "id": "doc1",
                    "name": "Thermo lecture 3",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-02-01T10:00:00Z",
                    "webViewLink": "https://docs.google.com/document/d/doc1/edit",
                },
                {
                    "id": "zip1",
                    "name": "archive.zip",
                    "mimeType": "application/zip",
                    "size": "999",
                },
            ],
            "nextPageToken": "page2",
        },
    )

    resp = client.get("/materials/drive/files")
    assert resp.status_code == 200
    body = resp.json()

    assert [f["drive_file_id"] for f in body["items"]] == ["doc1"]
    assert body["next_page_token"] == "page2"

    doc = body["items"][0]
    assert doc["upload_type"] == "text"
    # A Doc reaches Αθηνα as exported text, not as the document -- the picker
    # has to be able to say so.
    assert doc["exported"] is True
    assert doc["size"] is None
    assert doc["source_file_id"] is None


def test_picker_marks_files_already_imported(client, monkeypatch):
    connect_google()
    with connection() as conn:
        existing = repo.create_source_file(
            conn,
            filename="Thermo lecture 3",
            upload_type="text",
            origin="drive",
            drive_file_id="doc1",
        )

    stub_drive(
        monkeypatch,
        files={
            "files": [
                {
                    "id": "doc1",
                    "name": "Thermo lecture 3",
                    "mimeType": "application/vnd.google-apps.document",
                }
            ]
        },
    )

    body = client.get("/materials/drive/files").json()
    assert body["items"][0]["source_file_id"] == existing["id"]


def test_picker_passes_the_search_term_through(client, monkeypatch):
    connect_google()
    calls = stub_drive(monkeypatch, files={"files": []})

    client.get("/materials/drive/files", params={"search": "thermo"})
    assert "name contains 'thermo'" in calls["list"][0]["query"]


def test_picker_is_409_when_not_connected(client):
    """409, not 502: the request was fine and the fix is in Settings, so the
    UI needs to tell them apart to offer the right next step."""
    resp = client.get("/materials/drive/files")
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_picker_is_502_when_google_is_unreachable(client, monkeypatch):
    connect_google()

    async def fake_token():
        return "ya29.test"

    async def boom(*a, **k):
        raise google_drive.DriveError("could not reach Google Drive: timeout")

    monkeypatch.setattr(google_drive, "access_token", fake_token)
    monkeypatch.setattr(google_drive, "list_files", boom)

    assert client.get("/materials/drive/files").status_code == 502


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


DOC_META = {
    "id": "doc1",
    "name": "Thermo lecture 3",
    "mimeType": "application/vnd.google-apps.document",
    "modifiedTime": "2026-02-01T10:00:00Z",
    "webViewLink": "https://docs.google.com/document/d/doc1/edit",
}


def test_import_creates_a_pointer_row_with_no_bytes_on_disk(client, monkeypatch):
    """The whole Drive rescope: the original stays in the user's Drive and only
    the derived index lives here (migrations/005_drive_sources.sql)."""
    connect_google()
    stub_pipeline_llm(monkeypatch)
    stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())

    resp = client.post("/materials/drive/import", json={"file_ids": ["doc1"]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["rejected"] == {}
    file_id = body["accepted"][0]["source_file_id"]

    with connection() as conn:
        row = repo.get_source_file(conn, file_id)

    assert row["origin"] == "drive"
    assert row["stored_path"] is None
    assert row["drive_file_id"] == "doc1"
    assert row["drive_url"] == DOC_META["webViewLink"]
    assert row["drive_mime_type"] == DOC_META["mimeType"]
    # Nothing was written under uploads/.
    uploads = get_settings().uploads_path
    assert not uploads.exists() or not any(uploads.iterdir())


def test_import_ingests_the_fetched_text(client, monkeypatch):
    connect_google()
    stub_pipeline_llm(monkeypatch)
    calls = stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())

    body = client.post("/materials/drive/import", json={"file_ids": ["doc1"]}).json()
    file_id = body["accepted"][0]["source_file_id"]

    with connection() as conn:
        row = repo.get_source_file(conn, file_id)
        chunks = repo.chunks_for_source_file(conn, file_id)

    assert row["ingest_status"] == "ready", row["ingest_error"]
    assert chunks, "the fetched Drive bytes never became chunks"
    assert "Entropy" in chunks[0]["text"]
    # A Doc must be exported, never fetched with alt=media.
    assert calls["download"][0] == {"id": "doc1", "export_mime": "text/plain"}
    # Size is only knowable after the fetch, since Drive reports none for Docs.
    assert row["byte_size"] == len(LECTURE_TEXT.encode())


def test_reimport_updates_the_existing_row_rather_than_duplicating(
    client, monkeypatch
):
    connect_google()
    stub_pipeline_llm(monkeypatch)
    stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())
    first = client.post("/materials/drive/import", json={"file_ids": ["doc1"]}).json()

    renamed = {**DOC_META, "name": "Thermo lecture 3 (revised)"}
    stub_drive(monkeypatch, meta=renamed, content=LECTURE_TEXT.encode())
    second = client.post("/materials/drive/import", json={"file_ids": ["doc1"]}).json()

    assert (
        first["accepted"][0]["source_file_id"]
        == second["accepted"][0]["source_file_id"]
    )
    with connection() as conn:
        rows = [r for r in repo.list_source_files(conn) if r["origin"] == "drive"]
    assert len(rows) == 1
    # Drive is the source of truth for the name: it may have been renamed there.
    assert rows[0]["filename"] == "Thermo lecture 3 (revised)"


def test_import_rejects_unreadable_files_without_failing_the_batch(
    client, monkeypatch
):
    """A folder of course material routinely holds one file nobody can read;
    losing the other nine to it would be the wrong trade."""
    connect_google()
    stub_pipeline_llm(monkeypatch)
    stub_drive(
        monkeypatch,
        meta={
            "doc1": DOC_META,
            "zip1": {"id": "zip1", "name": "archive.zip", "mimeType": "application/zip"},
        },
        content=LECTURE_TEXT.encode(),
    )

    body = client.post(
        "/materials/drive/import", json={"file_ids": ["doc1", "zip1"]}
    ).json()

    assert len(body["accepted"]) == 1
    assert body["accepted"][0]["filename"] == "Thermo lecture 3"
    assert "archive.zip" in body["rejected"]


def test_import_is_409_when_drive_read_is_off(client):
    connect_google(drive_read=False)
    resp = client.post("/materials/drive/import", json={"file_ids": ["doc1"]})
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_import_refuses_a_file_already_being_ingested(client, monkeypatch):
    """_claim would refuse the second run anyway, but silently -- which would
    look like the import button simply did nothing."""
    connect_google()
    with connection() as conn:
        row = repo.create_source_file(
            conn,
            filename="Thermo lecture 3",
            upload_type="text",
            origin="drive",
            drive_file_id="doc1",
        )
        repo.set_status(conn, row["id"], "tagging")

    stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())
    body = client.post("/materials/drive/import", json={"file_ids": ["doc1"]}).json()

    assert body["accepted"] == []
    assert "already importing" in body["rejected"]["Thermo lecture 3"]


# --------------------------------------------------------------------------
# ingest and retry
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_retry_refetches_from_drive_rather_than_disk(monkeypatch):
    """There is no stored copy to fall back on, so a retry is a live re-fetch --
    which is also how edits made in Drive get picked up."""
    connect_google()
    stub_pipeline_llm(monkeypatch)
    calls = stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())

    with connection() as conn:
        row = repo.create_source_file(
            conn,
            filename="Thermo lecture 3",
            upload_type="text",
            origin="drive",
            drive_file_id="doc1",
            drive_mime_type=DOC_META["mimeType"],
        )

    await pipeline.ingest(row["id"])
    assert len(calls["download"]) == 1

    with connection() as conn:
        repo.set_status(conn, row["id"], "pending")
    await pipeline.ingest(row["id"])
    assert len(calls["download"]) == 2

    with connection() as conn:
        assert repo.get_source_file(conn, row["id"])["ingest_status"] == "ready"


@pytest.mark.anyio
async def test_drive_failure_is_recorded_on_the_row(monkeypatch):
    """The ingest runs detached with no caller to catch, so an unreachable
    Drive has to land somewhere the user can see it."""
    connect_google()
    stub_pipeline_llm(monkeypatch)

    async def fake_token():
        return "ya29.test"

    async def boom(*a, **k):
        raise google_drive.DriveError("Google Drive returned an error: rate limited")

    monkeypatch.setattr(google_drive, "access_token", fake_token)
    monkeypatch.setattr(google_drive, "download", boom)

    with connection() as conn:
        row = repo.create_source_file(
            conn,
            filename="Thermo lecture 3",
            upload_type="text",
            origin="drive",
            drive_file_id="doc1",
            drive_mime_type=DOC_META["mimeType"],
        )

    await pipeline.ingest(row["id"])

    with connection() as conn:
        after = repo.get_source_file(conn, row["id"])
    assert after["ingest_status"] == "failed"
    assert "rate limited" in after["ingest_error"]


@pytest.mark.anyio
async def test_successful_fetch_stamps_last_synced_at(monkeypatch):
    """Settings shows when the connection was last used, which is a more
    useful reassurance than when it was made."""
    connect_google()
    stub_pipeline_llm(monkeypatch)
    stub_drive(monkeypatch, meta=DOC_META, content=LECTURE_TEXT.encode())

    with connection() as conn:
        row = repo.create_source_file(
            conn,
            filename="Thermo lecture 3",
            upload_type="text",
            origin="drive",
            drive_file_id="doc1",
            drive_mime_type=DOC_META["mimeType"],
        )
        assert conn_repo.get(conn, conn_repo.GOOGLE_SLUG)["last_synced_at"] is None

    await pipeline.ingest(row["id"])

    with connection() as conn:
        assert conn_repo.get(conn, conn_repo.GOOGLE_SLUG)["last_synced_at"] is not None


@pytest.mark.anyio
async def test_local_uploads_still_read_from_disk(monkeypatch, tmp_path):
    """The Drive branch must not have changed the path every existing upload
    takes."""
    stub_pipeline_llm(monkeypatch)

    async def explode(*a, **k):
        raise AssertionError("a local upload reached the Drive client")

    monkeypatch.setattr(google_drive, "access_token", explode)

    stored = tmp_path / "notes.txt"
    stored.write_text(LECTURE_TEXT)
    with connection() as conn:
        row = repo.create_source_file(
            conn,
            filename="notes.txt",
            upload_type="text",
            stored_path=str(stored),
        )

    await pipeline.ingest(row["id"])

    with connection() as conn:
        after = repo.get_source_file(conn, row["id"])
    assert after["ingest_status"] == "ready", after["ingest_error"]


# --------------------------------------------------------------------------
# download transport
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_download_stops_at_the_size_limit(monkeypatch):
    """/export reports no size up front, so the ceiling has to be enforced
    against the streamed body or a huge Doc would be read in full."""
    import httpx

    body = b"x" * 5000

    def handler(request):
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    with pytest.raises(google_drive.DriveError, match="larger than"):
        await google_drive.download("tok", "doc1", export_mime=None, limit=1000)

    ok = await google_drive.download("tok", "doc1", export_mime=None, limit=10_000)
    assert ok == body
