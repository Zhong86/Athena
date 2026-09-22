"""The Google Drive credentials handoff.

Google itself is stubbed; SQLite, Fernet and the file writes are real. The whole
point of the feature is that two correctly-shaped files land on the Hermes host,
so faking the part that writes them would test nothing.

The transport runs in *local* mode for most tests, which also sidesteps
scripts/fake_hermes.py -- its `do_POST` json-decodes the body and would choke on
a multipart upload.
"""

import json
import os
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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
from app.connections import crypto, google_oauth, hermes_files  # noqa: E402
from app.connections import repository as repo  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_KEY = Fernet.generate_key().decode()

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


@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    """A clean DB and a clean Hermes config directory per test.

    Settings are cached with lru_cache, so the clear is what actually makes the
    monkeypatched env visible -- same reason test_hermes.py does it.
    """
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "athena.db"))
    monkeypatch.setenv("CONNECTIONS_SECRET_KEY", TEST_KEY)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_FILES_URL", "")
    monkeypatch.setenv("HERMES_FILES_TOKEN", "")
    monkeypatch.setenv("WARM_EMBEDDINGS", "false")
    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    init_db()
    yield tmp_path / "hermes"
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


@pytest.fixture
def client():
    # No lifespan: init_db already ran and the embedding warm-up has no place
    # in these tests.
    return TestClient(app)


def upload(client, payload=CLIENT_JSON, name="client_secret.json"):
    return client.post(
        "/connections/google/client",
        files={"file": (name, json.dumps(payload).encode(), "application/json")},
    )


def stub_google(monkeypatch, *, token=None, email="zhong@university.edu"):
    async def fake_exchange(client_conf, code):
        assert code, "exchange called without a code"
        return token or TOKEN_RESPONSE

    async def fake_email(access_token):
        return email

    monkeypatch.setattr(google_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(google_oauth, "fetch_account_email", fake_email)


# --------------------------------------------------------------------------
# the transport
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_local_write_is_private_and_atomic(fresh_env):
    await hermes_files.put_file(hermes_files.GOOGLE_TOKEN_FILE, {"token": "abc"})

    path = fresh_env / hermes_files.GOOGLE_TOKEN_FILE
    assert json.loads(path.read_text()) == {"token": "abc"}
    # A credential file must not be world- or group-readable.
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(fresh_env.stat().st_mode & 0o777) == "0o700"
    # No temp file left behind.
    assert [p.name for p in fresh_env.iterdir()] == [hermes_files.GOOGLE_TOKEN_FILE]


@pytest.mark.anyio
async def test_local_overwrite_replaces_content(fresh_env):
    await hermes_files.put_file(hermes_files.GOOGLE_TOKEN_FILE, {"v": 1})
    await hermes_files.put_file(hermes_files.GOOGLE_TOKEN_FILE, {"v": 2})
    path = fresh_env / hermes_files.GOOGLE_TOKEN_FILE
    assert json.loads(path.read_text()) == {"v": 2}

    await hermes_files.delete_file(hermes_files.GOOGLE_TOKEN_FILE)
    assert not path.exists()
    # Deleting what is already gone is not an error -- disconnect must be
    # idempotent.
    await hermes_files.delete_file(hermes_files.GOOGLE_TOKEN_FILE)


@pytest.mark.anyio
async def test_filename_allowlist_blocks_anything_else():
    with pytest.raises(hermes_files.HermesFileError):
        await hermes_files.put_file("../../root/.ssh/authorized_keys", {})


@pytest.mark.anyio
async def test_unconfigured_transport_refuses(monkeypatch):
    monkeypatch.setenv("HERMES_CONFIG_PATH", "")
    monkeypatch.setenv("HERMES_FILES_URL", "")
    get_settings.cache_clear()
    assert not hermes_files.configured()
    assert hermes_files.describe() == "unconfigured"
    with pytest.raises(hermes_files.HermesFileError, match="nowhere|no Hermes"):
        await hermes_files.put_file(hermes_files.GOOGLE_TOKEN_FILE, {})


@pytest.mark.anyio
async def test_sidecar_mode_sends_bearer_token(monkeypatch):
    """Same threaded-stub shape as test_hermes.py's integration test."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length) or "{}"),
                }
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HERMES_CONFIG_PATH", "")
        monkeypatch.setenv("HERMES_FILES_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("HERMES_FILES_TOKEN", "sidecar-secret")
        get_settings.cache_clear()

        assert hermes_files.describe().startswith("sidecar")
        await hermes_files.put_file(hermes_files.GOOGLE_TOKEN_FILE, {"token": "abc"})
    finally:
        server.shutdown()
        get_settings.cache_clear()

    assert received == [
        {
            "path": f"/credentials/{hermes_files.GOOGLE_TOKEN_FILE}",
            "authorization": "Bearer sidecar-secret",
            "body": {"token": "abc"},
        }
    ]


# --------------------------------------------------------------------------
# client-secret upload
# --------------------------------------------------------------------------


def test_upload_returns_consent_url_and_writes_client_file(client, fresh_env):
    resp = upload(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()

    url = body["authorize_url"]
    # access_type=offline and prompt=consent are what make Google return a
    # refresh token -- without one Hermes loses access within the hour.
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "client_id=123.apps.googleusercontent.com" in url
    assert "drive.readonly" in url
    assert body["redirect_uri"] == "http://localhost:9004/"
    assert body["connection"]["status"] == "authorizing"

    written = json.loads((fresh_env / hermes_files.GOOGLE_CLIENT_FILE).read_text())
    # Hermes expects the `installed` wrapper, not the bare client fields.
    assert written["installed"]["client_id"] == CLIENT_JSON["installed"]["client_id"]


def test_web_application_client_is_named_specifically(client):
    """Both file types are valid JSON from Google, so a generic 'malformed'
    message would send the user round in circles."""
    resp = upload(client, {"web": CLIENT_JSON["installed"]})
    assert resp.status_code == 400
    assert "Desktop app" in resp.json()["detail"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"not json at all", "valid JSON"),
        (json.dumps({"nonsense": 1}).encode(), "installed"),
        (json.dumps({"installed": {"client_id": "x"}}).encode(), "missing"),
    ],
)
def test_bad_client_files_are_rejected(client, payload, expected):
    resp = client.post(
        "/connections/google/client",
        files={"file": ("client_secret.json", payload, "application/json")},
    )
    assert resp.status_code == 400
    assert expected in resp.json()["detail"]


def test_empty_file_is_rejected(client):
    resp = client.post(
        "/connections/google/client",
        files={"file": ("client_secret.json", b"", "application/json")},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]


def test_oversized_file_is_rejected(client):
    resp = client.post(
        "/connections/google/client",
        files={"file": ("client_secret.json", b"x" * 70_000, "application/json")},
    )
    assert resp.status_code == 413


def test_reupload_updates_the_same_row(client):
    upload(client)
    upload(client)
    with connection() as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM connections").fetchone()
    # A second Google row would leave capability checks picking arbitrarily
    # between them.
    assert rows["n"] == 1


# --------------------------------------------------------------------------
# code exchange
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pasted",
    [
        "http://localhost:9004/?code=4/0AtestCode&scope=drive.readonly",
        "localhost:9004/?code=4/0AtestCode",
        "4/0AtestCode",
    ],
)
def test_exchange_accepts_url_or_bare_code(client, monkeypatch, fresh_env, pasted):
    upload(client)
    stub_google(monkeypatch)

    resp = client.post("/connections/google/exchange", json={"pasted": pasted})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected"
    assert body["account_label"] == "zhong@university.edu"
    assert body["capabilities"] == {"drive.read": True}
    assert "https://www.googleapis.com/auth/drive.readonly" in body["scopes"]


def test_exchange_writes_a_token_file_google_auth_can_read(
    client, monkeypatch, fresh_env
):
    """The shape is the whole risk here: a wrong field name or expiry format
    gives a connection that looks fine and does not work."""
    upload(client)
    stub_google(monkeypatch)
    client.post(
        "/connections/google/exchange",
        json={"pasted": "http://localhost:9004/?code=4/0AtestCode"},
    )

    path = fresh_env / hermes_files.GOOGLE_TOKEN_FILE
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(json.loads(path.read_text()))
    assert creds.refresh_token == TOKEN_RESPONSE["refresh_token"]
    assert creds.client_id == CLIENT_JSON["installed"]["client_id"]
    assert creds.expiry is not None
    # google-auth compares expiry against a naive utcnow(); an aware datetime
    # here would make every token look expired.
    assert creds.expiry.tzinfo is None


def test_declined_consent_is_explained(client, fresh_env):
    upload(client)
    resp = client.post(
        "/connections/google/exchange",
        json={"pasted": "http://localhost:9004/?error=access_denied"},
    )
    assert resp.status_code == 400
    assert "declined" in resp.json()["detail"]


def test_url_without_a_code_is_explained(client, fresh_env):
    upload(client)
    resp = client.post(
        "/connections/google/exchange", json={"pasted": "http://localhost:9004/"}
    )
    assert resp.status_code == 400
    assert "no code" in resp.json()["detail"]


def test_exchange_without_an_upload_is_a_conflict(client):
    resp = client.post("/connections/google/exchange", json={"pasted": "4/0Acode"})
    assert resp.status_code == 409


def test_missing_refresh_token_is_refused(client, monkeypatch, fresh_env):
    """Without one Hermes cannot refresh and access dies in an hour, so this is
    a failure now rather than a mystery later."""
    upload(client)
    no_refresh = {k: v for k, v in TOKEN_RESPONSE.items() if k != "refresh_token"}

    async def fake_post(url, data=None, **kwargs):
        raise AssertionError("should not reach Google")

    monkeypatch.setattr(
        google_oauth,
        "exchange_code",
        google_oauth.exchange_code,  # keep the real one
    )

    # Stub at the HTTP layer so the real exchange_code logic is what runs.
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return no_refresh

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", lambda **kw: FakeClient())

    resp = client.post("/connections/google/exchange", json={"pasted": "4/0Acode"})
    assert resp.status_code == 400
    assert "refresh token" in resp.json()["detail"]


# --------------------------------------------------------------------------
# the secret never leaves the repository layer (plan §5.2)
# --------------------------------------------------------------------------


def test_no_endpoint_exposes_the_credential(client, monkeypatch, fresh_env):
    upload(client)
    stub_google(monkeypatch)
    client.post("/connections/google/exchange", json={"pasted": "4/0AtestCode"})

    secret_values = (
        CLIENT_JSON["installed"]["client_secret"],
        TOKEN_RESPONSE["refresh_token"],
        TOKEN_RESPONSE["access_token"],
    )

    for path in ("/connections", "/connections"):
        body = client.get(path).text
        for value in secret_values:
            assert value not in body, f"{value!r} leaked through {path}"

    # And the response model has no field it could arrive through.
    from app.connections.schemas import ConnectionOut

    assert "secret" not in ConnectionOut.model_fields


def test_secret_is_encrypted_at_rest(client, fresh_env):
    upload(client)
    with connection() as conn:
        row = conn.execute("SELECT secret FROM connections").fetchone()

    assert CLIENT_JSON["installed"]["client_secret"] not in row["secret"]
    # Still recoverable through the one decrypting read.
    with connection() as conn:
        stored = repo.get_credentials(conn, repo.GOOGLE_SLUG)
    assert stored["client_secret"] == CLIENT_JSON["installed"]["client_secret"]


def test_missing_key_refuses_instead_of_storing_plaintext(client, monkeypatch):
    monkeypatch.setenv("CONNECTIONS_SECRET_KEY", "")
    get_settings.cache_clear()

    assert not crypto.available()
    resp = upload(client)
    assert resp.status_code == 409
    assert "CONNECTIONS_SECRET_KEY" in resp.json()["detail"]

    with connection() as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM connections").fetchone()
    assert rows["n"] == 0

    # And the UI is told up front rather than after the user picks a file.
    assert client.get("/connections").json()["secrets_ready"] is False


# --------------------------------------------------------------------------
# capabilities and disconnect
# --------------------------------------------------------------------------


def test_capability_can_be_toggled_off_and_on(client, monkeypatch, fresh_env):
    upload(client)
    stub_google(monkeypatch)
    client.post("/connections/google/exchange", json={"pasted": "4/0AtestCode"})

    off = client.put(
        "/connections/google/capabilities/drive.read", json={"enabled": False}
    )
    assert off.status_code == 200
    assert off.json()["capabilities"] == {"drive.read": False}

    on = client.put(
        "/connections/google/capabilities/drive.read", json={"enabled": True}
    )
    assert on.json()["capabilities"] == {"drive.read": True}


def test_ungranted_capability_cannot_be_enabled(client, monkeypatch, fresh_env):
    """Granted is not permitted, and a capability whose scope was never granted
    must not be creatable from the toggle."""
    upload(client)
    stub_google(monkeypatch, token={**TOKEN_RESPONSE, "scope": "openid"})
    client.post("/connections/google/exchange", json={"pasted": "4/0AtestCode"})

    resp = client.put(
        "/connections/google/capabilities/drive.read", json={"enabled": True}
    )
    assert resp.status_code == 409
    assert "never granted" in resp.json()["detail"]


def test_unknown_capability_is_404(client, monkeypatch, fresh_env):
    upload(client)
    resp = client.put(
        "/connections/google/capabilities/drive.write", json={"enabled": True}
    )
    assert resp.status_code == 404


def test_disconnect_clears_everything(client, monkeypatch, fresh_env):
    upload(client)
    stub_google(monkeypatch)
    client.post("/connections/google/exchange", json={"pasted": "4/0AtestCode"})

    async def fake_revoke(refresh_token):
        assert refresh_token == TOKEN_RESPONSE["refresh_token"]
        return None

    monkeypatch.setattr(google_oauth, "revoke", fake_revoke)

    resp = client.delete("/connections/google")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["warning"] is None
    assert body["connection"]["status"] == "disconnected"
    assert body["connection"]["account_label"] is None
    # A capability left enabled on a disconnected row would make the toggle lie.
    assert body["connection"]["capabilities"] == {}

    assert not (fresh_env / hermes_files.GOOGLE_TOKEN_FILE).exists()
    assert not (fresh_env / hermes_files.GOOGLE_CLIENT_FILE).exists()
    with connection() as conn:
        assert repo.get_credentials(conn, repo.GOOGLE_SLUG) is None


def test_disconnect_still_clears_when_revoke_fails(client, monkeypatch, fresh_env):
    """A user who clicks Disconnect must end up disconnected here whether or
    not Google is reachable -- but should be told it was untidy."""
    upload(client)
    stub_google(monkeypatch)
    client.post("/connections/google/exchange", json={"pasted": "4/0AtestCode"})

    async def failing_revoke(refresh_token):
        return "could not reach Google to revoke the token: boom"

    monkeypatch.setattr(google_oauth, "revoke", failing_revoke)

    body = client.delete("/connections/google").json()
    assert body["connection"]["status"] == "disconnected"
    assert "revoke" in body["warning"]
    with connection() as conn:
        assert repo.get_credentials(conn, repo.GOOGLE_SLUG) is None


def test_disconnect_when_never_connected_is_404(client):
    assert client.delete("/connections/google").status_code == 404
