"""`materials_context` -- loaded before personalization.

A plain read, deliberately kept out of the nodes: a node that queries SQLite
directly cannot be tested without a database, and the spec puts this in state
precisely so `personalize_decomposition` is a pure function of its inputs.

`calendar_context` used to sit beside it and was cut with the rest of the
deadline system (Zhong, 2026-09-21).
"""

import sqlite3
from typing import Any


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
