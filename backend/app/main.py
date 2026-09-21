from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import hermes
from app.config import get_settings
from app.db import init_db


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


@app.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "sqlite": settings.sqlite_path.exists(),
        "hermes": await hermes.ping(),
    }


class EchoRequest(BaseModel):
    prompt: str


@app.post("/agent/echo")
async def agent_echo(body: EchoRequest) -> dict[str, str]:
    """Step 1 smoke test: a trivial prompt round-trip through Hermes."""
    return {"reply": await hermes.complete(body.prompt)}
