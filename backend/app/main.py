from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import hermes
from app.config import get_settings
from app.db import connection, init_db
from app.migrations import current_version, pending_count
from app.routers import sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Athena", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(sessions.router)


@app.get("/health")
async def health() -> dict[str, object]:
    """Reports migration state, not just file existence -- an empty or
    half-migrated DB must not read as healthy."""
    with connection() as conn:
        version = current_version(conn)
        pending = pending_count(conn)

    return {
        "status": "ok" if version and not pending else "degraded",
        "sqlite": {
            "path": str(get_settings().sqlite_path),
            "schema_version": version,
            "pending_migrations": pending,
        },
        "hermes": await hermes.ping(),
    }


class EchoRequest(BaseModel):
    prompt: str


@app.post("/agent/echo")
async def agent_echo(body: EchoRequest) -> dict[str, str]:
    """Step 1 smoke test: a trivial prompt round-trip through Hermes."""
    return {"reply": await hermes.complete(body.prompt)}
