"""`topics_with_material` -- loaded by `choose_topic`.

A plain read, kept out of the node for the same reason `app.goals.context`
keeps `materials_context` out of `personalize_decomposition`: a node that
queries SQLite directly cannot be swapped out in a node-level test without a
database.
"""

import sqlite3
from typing import Any

from app.materials import repository as materials_repo


def topics_with_material(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Topics with at least one chunk -- the only ones a quiz can be grounded
    in. A topic with zero chunks (freshly auto-created, or emptied by a
    deletion) is worse to offer than not offering it at all."""
    return [t for t in materials_repo.list_topics(conn) if t.get("chunk_count")]
