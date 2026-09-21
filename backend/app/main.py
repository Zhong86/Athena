import logging
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import embeddings, hermes
from app.config import get_settings
from app.dashboard import router as dashboard_router
from app.db import connection, init_db
from app.goals import router as goals_router
from app.materials import router as materials_router
from app.migrations import current_version, pending_count
from app.quizzes import router as quizzes_router
from app.sessions import router as sessions_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().warm_embeddings:
        # In a thread, and failures are logged rather than raised: a missing
        # model download should degrade ingestion, not stop the API booting.
        async def _warm() -> None:
            try:
                await anyio.to_thread.run_sync(embeddings.warm)
            except Exception:
                logging.getLogger(__name__).exception("embedding model warm-up failed")

        async with anyio.create_task_group() as tg:
            tg.start_soon(_warm)
            yield
            tg.cancel_scope.cancel()
    else:
        yield


app = FastAPI(title="Athena", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(sessions_router.router)
app.include_router(materials_router.router)
app.include_router(goals_router.router)
app.include_router(quizzes_router.router)
app.include_router(dashboard_router.router)


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
