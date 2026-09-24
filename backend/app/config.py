from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Hermes api_server adapter
    hermes_base_url: str = "http://127.0.0.1:8642"
    api_server_key: str = ""
    hermes_session_key: str = "athena-local"

    # Where Hermes reads its credential files from, and how we get them there.
    # Exactly one of these two is configured; see app/connections/hermes_files.py.
    #
    # local: a filesystem path the backend can write. In production Hermes runs
    # on the same VPS, so this is the host's ~/.hermes bind-mounted into the
    # container -- the credentials never touch a network.
    hermes_config_path: Path | None = None
    # sidecar: a remote Hermes box (debugging). Base URL of hermes-sidecar/.
    hermes_files_url: str = ""
    hermes_files_token: str = ""

    # Bearer token Hermes must send to call Athena's MCP tool server (see
    # app/mcp_server.py, mounted at /mcp). Empty means /mcp refuses every
    # request with 503 rather than running open -- same posture as
    # hermes_files_token / hermes-sidecar's require_token.
    mcp_token: str = ""

    # Encrypts `connections.secret` at rest (Fernet). Absent means the connect
    # endpoints refuse to start a flow rather than writing a credential in
    # plaintext -- see plans/athena-connections-plan_v.0.2.md §5.1.
    connections_secret_key: str = ""

    # Google OAuth. The client identity itself is uploaded by the user, not
    # configured here -- these only shape the consent request.
    #
    # Drive read plus the address, which is the only way to label the connected
    # account in the UI. Widen it here if a Hermes skill needs more; enabling
    # extra APIs in the Cloud Console is harmless on its own.
    google_oauth_scopes: str = (
        "https://www.googleapis.com/auth/drive.readonly "
        "https://www.googleapis.com/auth/userinfo.email"
    )
    # Desktop-app clients can only redirect to loopback (Google disabled the
    # out-of-band flow in 2022). This is a port on the *user's* machine, and
    # nothing listens on it: the browser fails to load and they copy the code
    # out of the address bar. Pick something unlikely to be occupied.
    google_loopback_port: int = 9004

    # Storage
    sqlite_path: Path = BACKEND_DIR / "data" / "athena.db"
    lancedb_path: Path = BACKEND_DIR / "data" / "lancedb"
    # Original uploaded bytes, kept so a failed extract can be retried without
    # asking the user to upload again.
    uploads_path: Path = BACKEND_DIR / "data" / "uploads"
    # A drop folder the user can put files into directly, outside the upload
    # API. The gather graph (app/materials/gather/) scans it; an accepted file
    # is moved into uploads_path, a rejected one into <this>/.skipped/.
    materials_inbox_path: Path = BACKEND_DIR / "data" / "materials_inbox"
    # Pinned so the ~130MB ONNX download lands inside the project rather than in
    # the ambient HF cache -- matters on a fresh VPS where $HOME may be wiped.
    fastembed_cache: Path = BACKEND_DIR / "data" / "models"

    # Materials ingestion
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    max_upload_bytes: int = 10 * 1024 * 1024
    # Off in tests: warming downloads ~130MB on a cold cache, which no test
    # should ever trigger.
    warm_embeddings: bool = True

    # Materials gather (cron-triggered auto-discovery)
    # Placeholders -- tune once real Drive/inbox volume is seen.
    materials_gather_max_imports: int = 20
    materials_gather_drive_scan_cap: int = 300
    # Drive's API has no recursive folder search -- a scoped scan walks the
    # tree itself (see gather/scan_drive.py), one list_files call per folder.
    # This bounds how many folders that walk can visit, independent of the
    # file-count cap above, so a folder scope pointed at a huge tree can't
    # turn one gather run into hundreds of Drive calls.
    materials_gather_drive_folder_cap: int = 100
    # Shared secret for POST /materials/gather/run. Empty means the endpoint
    # refuses every request -- an unauthenticated auto-import-and-ingest
    # endpoint must not be reachable by default.
    materials_gather_token: str = ""

    # Dev-only bulk-delete switch (POST /materials/gather/reset). Never set
    # this in a deployed .env -- see that endpoint's docstring for what it
    # wipes. Defaults off so the endpoint 404s (not just refuses) unless a
    # developer has deliberately opted in.
    test_mode: bool = False

    # Frontend origins allowed through CORS (comma-separated).
    # Dev port drifts when 3000 is taken by another project, so allow both.
    frontend_origin: str = "http://localhost:3000,http://localhost:3001"

    @field_validator("hermes_config_path", mode="before")
    @classmethod
    def _normalise_hermes_config_path(cls, value: object) -> object:
        """`HERMES_CONFIG_PATH=` must mean "unset", not `Path(".")`.

        Both .env.example files tell sidecar users to leave this empty, and
        pydantic would otherwise parse the empty string into the current working
        directory -- which is truthy, so hermes_files would pick local mode and
        write the credentials into the process's cwd instead of sending them to
        the sidecar.

        A relative path is anchored to BACKEND_DIR for the same reason the
        storage paths above are: it otherwise lands wherever uvicorn happened to
        be started from, which is the repo root in dev and /app in the
        container. Credentials are not a thing to misplace.
        """
        if isinstance(value, str) and not value.strip():
            return None
        if value is None:
            return None
        path = Path(value)  # type: ignore[arg-type]
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def google_scope_list(self) -> list[str]:
        return [s.strip() for s in self.google_oauth_scopes.split() if s.strip()]

    @property
    def google_redirect_uri(self) -> str:
        return f"http://localhost:{self.google_loopback_port}/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
