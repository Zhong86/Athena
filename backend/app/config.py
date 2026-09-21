from functools import lru_cache
from pathlib import Path

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

    # Google OAuth (Drive scope only -- Drive-backed materials ingest)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Storage
    sqlite_path: Path = BACKEND_DIR / "data" / "athena.db"
    lancedb_path: Path = BACKEND_DIR / "data" / "lancedb"
    # Original uploaded bytes, kept so a failed extract can be retried without
    # asking the user to upload again.
    uploads_path: Path = BACKEND_DIR / "data" / "uploads"
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

    # Frontend origins allowed through CORS (comma-separated).
    # Dev port drifts when 3000 is taken by another project, so allow both.
    frontend_origin: str = "http://localhost:3000,http://localhost:3001"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
