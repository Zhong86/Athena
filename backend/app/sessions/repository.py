"""SQL for the `sessions` table.

Transcripts live in `sessions.payload` as {"messages": [{role, content, at}]}.
Keeping SQL here rather than in the router keeps Step 8's log queries in one
place when quizzes and cron results start writing rows too.
"""

import json
import sqlite3
from typing import Any

SESSION_TYPES = ("chat", "quiz", "cron", "agent_action")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    session = dict(row)
    session["payload"] = json.loads(session["payload"]) if session["payload"] else None
    return session


def create(
    conn: sqlite3.Connection,
    *,
    type: str,
    payload: dict[str, Any] | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO sessions (type, payload, summary) VALUES (?, ?, ?) RETURNING *",
        (type, json.dumps(payload) if payload is not None else None, summary),
    )
    return _row_to_dict(cur.fetchone())


def get(conn: sqlite3.Connection, session_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _filters(type: str | None, archived: bool) -> tuple[str, list[Any]]:
    """Shared WHERE for list_/count so the two can never disagree about which
    rows are in the log -- a mismatch would show `5 chats` above 4 rows."""
    clauses: list[str] = []
    params: list[Any] = []
    if type:
        clauses.append("type = ?")
        params.append(type)
    clauses.append("archived_at IS NOT NULL" if archived else "archived_at IS NULL")
    return " WHERE " + " AND ".join(clauses), params


def list_(
    conn: sqlite3.Connection,
    *,
    type: str | None = None,
    archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Newest first -- the Sessions log is reverse-chronological."""
    where, params = _filters(type, archived)
    sql = f"SELECT * FROM sessions{where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?"
    return [_row_to_dict(r) for r in conn.execute(sql, [*params, limit, offset])]


def count(conn: sqlite3.Connection, *, type: str | None = None, archived: bool = False) -> int:
    where, params = _filters(type, archived)
    return conn.execute(f"SELECT COUNT(*) FROM sessions{where}", params).fetchone()[0]


def append_messages(
    conn: sqlite3.Connection, session_id: int, new_messages: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Append to the stored transcript, read-modify-write inside the caller's
    transaction so a concurrent turn cannot clobber it."""
    row = conn.execute(
        "SELECT payload FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None

    payload = json.loads(row["payload"]) if row["payload"] else {}
    payload.setdefault("messages", []).extend(new_messages)
    conn.execute(
        "UPDATE sessions SET payload = ? WHERE id = ?",
        (json.dumps(payload), session_id),
    )
    return payload


def update(
    conn: sqlite3.Connection,
    session_id: int,
    *,
    title: str | None = None,
    archived_at: str | None = None,
    clear_title: bool = False,
    clear_archived: bool = False,
) -> dict[str, Any] | None:
    """Patch the renameable/archivable columns. The explicit clear_* flags
    exist because `None` here means "leave alone", not "set to NULL"."""
    sets: list[str] = []
    params: list[Any] = []
    if clear_title:
        sets.append("title = NULL")
    elif title is not None:
        sets.append("title = ?")
        params.append(title)
    if clear_archived:
        sets.append("archived_at = NULL")
    elif archived_at is not None:
        sets.append("archived_at = ?")
        params.append(archived_at)

    if not sets:
        return get(conn, session_id)

    row = conn.execute(
        f"UPDATE sessions SET {', '.join(sets)} WHERE id = ? RETURNING *",
        [*params, session_id],
    ).fetchone()
    return _row_to_dict(row) if row else None


def delete(conn: sqlite3.Connection, session_id: int) -> bool:
    """Permanent. quiz_attempts cascade off the session row."""
    return conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0


def set_summary(conn: sqlite3.Connection, session_id: int, summary: str) -> None:
    conn.execute("UPDATE sessions SET summary = ? WHERE id = ?", (summary, session_id))


def transcript(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    """Strip stored metadata down to the role/content pairs Hermes expects."""
    if not payload:
        return []
    return [
        {"role": m["role"], "content": m["content"]} for m in payload.get("messages", [])
    ]
