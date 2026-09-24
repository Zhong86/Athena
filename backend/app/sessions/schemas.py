from typing import Any, Literal

from pydantic import BaseModel, Field

SessionType = Literal["chat", "quiz", "cron", "agent_action"]


class SessionCreate(BaseModel):
    type: SessionType = "chat"
    payload: dict[str, Any] | None = None
    summary: str | None = None


class SessionUpdate(BaseModel):
    """Every field is optional; omitting one leaves it alone. Sending
    `title: null` clears the override so the derived title comes back."""

    title: str | None = Field(None, max_length=200)
    archived: bool | None = None


class Session(BaseModel):
    id: int
    type: SessionType
    started_at: str
    payload: dict[str, Any] | None = None
    summary: str | None = None
    title: str | None = None
    archived_at: str | None = None


class SessionPage(BaseModel):
    items: list[Session]
    total: int
    limit: int
    offset: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)


class ChatReply(BaseModel):
    session_id: int
    reply: str
    trace: list[dict[str, Any]] = Field(default_factory=list)
