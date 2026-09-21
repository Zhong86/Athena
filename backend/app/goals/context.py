"""`materials_context` and `calendar_context` -- loaded before personalization.

Both are plain reads, deliberately kept out of the nodes: a node that queries
SQLite directly cannot be tested without a database, and the spec puts these in
state precisely so `personalize_decomposition` is a pure function of its inputs.
"""

import sqlite3
from typing import Any

from app.ranking import Deadline

# Matches the plan's default: far enough out to catch a midterm, near enough that
# next semester's events do not reorder this week's work.
DEADLINE_WINDOW_DAYS = 21


def materials_context(conn: sqlite3.Connection) -> dict[str, Any]:
    """Topic strengths + how much material backs each one.

    No vector search here -- that happens per milestone inside the node. This is
    the catalogue the node needs to reason about coverage at all.
    """
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.description, t.user_understanding,
               COUNT(c.id) AS chunk_count
        FROM topics t
        LEFT JOIN chunks c ON c.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.name
        """
    ).fetchall()
    return {
        "topics": [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                # -1 is "no signal yet", not "weak". app.ranking keeps them apart.
                "user_understanding": row["user_understanding"],
                "chunk_count": row["chunk_count"],
            }
            for row in rows
        ]
    }


def calendar_context(
    conn: sqlite3.Connection, *, days: int = DEADLINE_WINDOW_DAYS
) -> dict[str, Any]:
    """Deadlines inside the window, as `app.ranking.Deadline` payloads.

    Step 5 (Google Calendar) is not built yet -- there is no `calendar_events`
    table to read, so this always returns `{"deadlines": []}`. That is a fully
    supported state, not a degradation to paper over: no deadlines means no
    reordering pressure and no "due in 3 days" clauses in any reason. Inventing
    a date to make the copy look richer would put a claim on screen that
    nothing backs. `conn` and `days` stay in the signature so the call site in
    `personalize_decomposition` does not change shape when Step 5 lands.
    """
    del conn, days
    return {"deadlines": []}


def deadlines_from(context: dict[str, Any] | None) -> list[Deadline]:
    """State dict -> the dataclass `app.ranking` takes.

    The conversion exists because state has to be JSON-serialisable for the
    checkpointer, so it cannot hold dataclasses.
    """
    if not context:
        return []
    return [
        Deadline(
            title=item["title"],
            due_at=item.get("due_at", ""),
            days_until=int(item.get("days_until", 0)),
        )
        for item in context.get("deadlines", [])
    ]
