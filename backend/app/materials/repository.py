"""SQL for `source_files`, `chunks` and `topics`.

SQLite is the source of truth for materials; LanceDB holds only vectors and is
rebuildable from these tables. Nothing in here touches the vector store -- that
ordering (SQLite first, Lance second) is what makes the rebuild possible.
"""

import sqlite3
from typing import Any

from app.clock import utc_now_iso
from app.materials.ingest.chunker import Chunk

INGEST_STATUSES = (
    "pending",
    "extracting",
    "tagging",
    "embedding",
    "ready",
    "failed",
)
# Only these are safe to (re)start an ingest from; anything else means a run is
# already in flight and a second one would duplicate chunks.
RESTARTABLE = ("pending", "failed")


# --------------------------------------------------------------------------
# source_files
# --------------------------------------------------------------------------


def create_source_file(
    conn: sqlite3.Connection,
    *,
    filename: str,
    upload_type: str,
    byte_size: int | None = None,
    stored_path: str | None = None,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO source_files (filename, upload_type, byte_size, stored_path)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (filename, upload_type, byte_size, stored_path),
    )
    return dict(cur.fetchone())


def get_source_file(conn: sqlite3.Connection, file_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM source_files WHERE id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


def list_source_files(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM source_files ORDER BY uploaded_at DESC, id DESC"
        )
    ]


def set_status(
    conn: sqlite3.Connection,
    file_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Clears any previous error unless a new one is supplied, so a successful
    retry does not leave a stale message on the row."""
    conn.execute(
        "UPDATE source_files SET ingest_status = ?, ingest_error = ? WHERE id = ?",
        (status, error, file_id),
    )


def set_chunk_count(conn: sqlite3.Connection, file_id: int, count: int) -> None:
    conn.execute(
        "UPDATE source_files SET chunk_count = ? WHERE id = ?", (count, file_id)
    )


def delete_source_file(conn: sqlite3.Connection, file_id: int) -> bool:
    """Chunks go with it via ON DELETE CASCADE. The caller is responsible for
    purging the matching vectors -- SQL cannot reach LanceDB."""
    cur = conn.execute("DELETE FROM source_files WHERE id = ?", (file_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------
# chunks
# --------------------------------------------------------------------------


def replace_chunks(
    conn: sqlite3.Connection, file_id: int, chunks: list[Chunk]
) -> list[int]:
    """Write a file's chunks, discarding any from a previous attempt.

    Replacing rather than appending makes a retry after a failed run idempotent:
    a second extract of the same file cannot leave two copies behind.
    """
    conn.execute("DELETE FROM chunks WHERE source_file_id = ?", (file_id,))

    ids: list[int] = []
    for order_index, c in enumerate(chunks):
        cur = conn.execute(
            """
            INSERT INTO chunks
                (source_file_id, topic_id, text, order_index, char_start, char_end)
            VALUES (?, NULL, ?, ?, ?, ?)
            RETURNING id
            """,
            (file_id, c.text, order_index, c.char_start, c.char_end),
        )
        ids.append(cur.fetchone()["id"])

    set_chunk_count(conn, file_id, len(ids))
    return ids


def chunks_for_source_file(
    conn: sqlite3.Connection, file_id: int
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chunks WHERE source_file_id = ? ORDER BY order_index",
            (file_id,),
        )
    ]


def chunks_for_topic(
    conn: sqlite3.Connection, topic_id: int, *, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.*, sf.filename, sf.upload_type
            FROM chunks c
            JOIN source_files sf ON sf.id = c.source_file_id
            WHERE c.topic_id = ?
            ORDER BY c.source_file_id, c.order_index
            LIMIT ? OFFSET ?
            """,
            (topic_id, limit, offset),
        )
    ]


def count_chunks_for_topic(conn: sqlite3.Connection, topic_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE topic_id = ?", (topic_id,)
    ).fetchone()[0]


def assign_topics(
    conn: sqlite3.Connection, assignments: dict[int, int | None]
) -> None:
    """Bulk `chunk_id -> topic_id`. A None value is left explicitly unassigned:
    the tagger could not place that chunk, and it stays searchable but off the
    topic pages."""
    conn.executemany(
        "UPDATE chunks SET topic_id = ? WHERE id = ?",
        [(topic_id, chunk_id) for chunk_id, topic_id in assignments.items()],
    )


def set_embedding_refs(conn: sqlite3.Connection, refs: dict[int, str]) -> None:
    conn.executemany(
        "UPDATE chunks SET embedding_ref = ? WHERE id = ?",
        [(ref, chunk_id) for chunk_id, ref in refs.items()],
    )


def get_chunks(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, dict]:
    """Hydrate search hits in one round-trip rather than one query per hit."""
    if not chunk_ids:
        return {}

    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT c.id, c.text, c.topic_id, c.source_file_id,
               t.name AS topic_name, sf.filename AS source_filename
        FROM chunks c
        LEFT JOIN topics t ON t.id = c.topic_id
        JOIN source_files sf ON sf.id = c.source_file_id
        WHERE c.id IN ({placeholders})
        """,
        chunk_ids,
    )
    return {r["id"]: dict(r) for r in rows}


# --------------------------------------------------------------------------
# topics
# --------------------------------------------------------------------------


def get_topic(conn: sqlite3.Connection, topic_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return dict(row) if row else None


def find_topic_by_name(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    """Case-insensitive exact match, then a prefix-ish LIKE.

    Both matter because callers are models: the tagger proposes "entropy" for an
    existing "Entropy", and `search_materials` gets whatever the agent decided
    to type.
    """
    row = conn.execute(
        "SELECT * FROM topics WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return dict(row)

    row = conn.execute(
        "SELECT * FROM topics WHERE name LIKE ? COLLATE NOCASE ORDER BY LENGTH(name) LIMIT 1",
        (f"%{name}%",),
    ).fetchone()
    return dict(row) if row else None


def create_topic(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str | None = None,
    auto_created: bool = True,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO topics (name, description, created_at, auto_created)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (name, description, utc_now_iso(), 1 if auto_created else 0),
    )
    return dict(cur.fetchone())


def list_topics_for_prompt(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The shape the tagger puts in front of the model: just enough to decide
    whether a chunk belongs to an existing topic."""
    return [
        dict(r)
        for r in conn.execute("SELECT id, name, description FROM topics ORDER BY name")
    ]


def list_topics(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Materials index: every topic with its chunk and distinct-source counts."""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT t.*,
                   COUNT(c.id) AS chunk_count,
                   COUNT(DISTINCT c.source_file_id) AS source_count
            FROM topics t
            LEFT JOIN chunks c ON c.topic_id = t.id
            GROUP BY t.id
            ORDER BY t.name
            """
        )
    ]


def sources_for_topic(conn: sqlite3.Connection, topic_id: int) -> list[dict[str, Any]]:
    """Backs the "9 of 34 chunks tagged to Entropy · rest tagged to Heat
    transfer, Second law" row on the topic page.

    Two queries, not one per source: the per-file counts, then every *other*
    topic those same files touch.
    """
    files = [
        dict(r)
        for r in conn.execute(
            """
            SELECT sf.id AS source_file_id, sf.filename, sf.upload_type,
                   sf.uploaded_at, sf.ingest_status,
                   COUNT(c.id) AS chunks_total,
                   SUM(CASE WHEN c.topic_id = ? THEN 1 ELSE 0 END) AS chunks_in_topic
            FROM source_files sf
            JOIN chunks c ON c.source_file_id = sf.id
            WHERE sf.id IN (SELECT DISTINCT source_file_id FROM chunks WHERE topic_id = ?)
            GROUP BY sf.id
            ORDER BY sf.uploaded_at DESC, sf.id DESC
            """,
            (topic_id, topic_id),
        )
    ]
    if not files:
        return []

    placeholders = ",".join("?" * len(files))
    file_ids = [f["source_file_id"] for f in files]
    others: dict[int, list[str]] = {}
    for r in conn.execute(
        f"""
        SELECT DISTINCT c.source_file_id, t.name
        FROM chunks c
        JOIN topics t ON t.id = c.topic_id
        WHERE c.source_file_id IN ({placeholders}) AND c.topic_id != ?
        ORDER BY t.name
        """,
        [*file_ids, topic_id],
    ):
        others.setdefault(r["source_file_id"], []).append(r["name"])

    for f in files:
        f["other_topics"] = others.get(f["source_file_id"], [])
    return files
