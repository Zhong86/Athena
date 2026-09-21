from fastapi import APIRouter, HTTPException, Query

from agent import hermes
from app.clock import utc_now_iso
from app.db import connection
from app.sessions import repository as repo
from app.sessions.schemas import (
    ChatReply,
    ChatRequest,
    Session,
    SessionCreate,
    SessionPage,
    SessionType,
    SessionUpdate,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

SYSTEM_PROMPT = (
    "You are Athena, a study agent. Be concise and concrete. "
    "When the student reveals what they do or do not understand, say so plainly."
)


@router.get("", response_model=SessionPage)
def list_sessions(
    type: SessionType | None = Query(None, description="Sessions log filter bar"),
    archived: bool = Query(False, description="List the archive instead of the log"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SessionPage:
    with connection() as conn:
        return SessionPage(
            items=repo.list_(
                conn, type=type, archived=archived, limit=limit, offset=offset
            ),
            total=repo.count(conn, type=type, archived=archived),
            limit=limit,
            offset=offset,
        )


@router.post("", response_model=Session, status_code=201)
def create_session(body: SessionCreate) -> Session:
    with connection() as conn:
        return Session(**repo.create(conn, **body.model_dump()))


@router.get("/{session_id}", response_model=Session)
def get_session(session_id: int) -> Session:
    with connection() as conn:
        session = repo.get(conn, session_id)
    if session is None:
        raise HTTPException(404, f"session {session_id} not found")
    return Session(**session)


@router.patch("/{session_id}", response_model=Session)
def update_session(session_id: int, body: SessionUpdate) -> Session:
    """Rename and archive/unarchive. Fields left out of the body are untouched;
    an explicit `"title": null` drops back to the derived title."""
    sent = body.model_fields_set
    title = body.title.strip() if body.title else None
    if "title" in sent and body.title is not None and not title:
        raise HTTPException(422, "title cannot be blank")

    with connection() as conn:
        updated = repo.update(
            conn,
            session_id,
            title=title,
            clear_title="title" in sent and body.title is None,
            archived_at=utc_now_iso() if body.archived else None,
            clear_archived=body.archived is False,
        )
    if updated is None:
        raise HTTPException(404, f"session {session_id} not found")
    return Session(**updated)


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int) -> None:
    with connection() as conn:
        if not repo.delete(conn, session_id):
            raise HTTPException(404, f"session {session_id} not found")


@router.post("/{session_id}/chat", response_model=ChatReply)
async def chat(session_id: int, body: ChatRequest) -> ChatReply:
    """One turn: persist the user message, call Hermes with the full
    transcript, persist the reply."""
    with connection() as conn:
        session = repo.get(conn, session_id)
        if session is None:
            raise HTTPException(404, f"session {session_id} not found")
        if session["type"] != "chat":
            raise HTTPException(409, f"session {session_id} is a {session['type']} session")
        history = repo.transcript(session["payload"])

    history.append({"role": "user", "content": body.message})

    try:
        reply = await hermes.chat(
            history, session_id=str(session_id), system=SYSTEM_PROMPT
        )
    except hermes.HermesError as exc:
        # The user's message is deliberately not persisted on failure, so a
        # retry does not duplicate it in the transcript.
        raise HTTPException(502, f"agent unavailable: {exc}") from exc

    with connection() as conn:
        repo.append_messages(
            conn,
            session_id,
            [
                {"role": "user", "content": body.message, "at": utc_now_iso()},
                {"role": "assistant", "content": reply, "at": utc_now_iso()},
            ],
        )

    return ChatReply(session_id=session_id, reply=reply)
