"""LanceDB: chunk embedding vectors, and nothing else.

This store is *derived*. Every column here also exists in SQLite, so the table
can be dropped and rebuilt from `chunks` at any time -- which is the whole
reason nothing is allowed to live here exclusively. `text` is duplicated purely
so a search can return snippets without a second round-trip.

Writes go SQLite-first, Lance-second. A crash between the two leaves orphaned
SQLite rows with `embedding_ref IS NULL`, which a retry fixes; the reverse
order would leave vectors pointing at chunks that do not exist.
"""

import uuid
from functools import lru_cache
from typing import Any

from app.config import get_settings

TABLE_NAME = "chunks"


def _schema():
    import pyarrow as pa

    dim = get_settings().embedding_dim
    return pa.schema(
        [
            pa.field("embedding_ref", pa.string(), nullable=False),
            pa.field("chunk_id", pa.int64(), nullable=False),
            # Nullable, mirroring SQLite: a chunk the tagger could not place is
            # still embedded and still findable in an unfiltered search.
            pa.field("topic_id", pa.int64(), nullable=True),
            pa.field("source_file_id", pa.int64(), nullable=False),
            pa.field("text", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dim), nullable=False),
        ]
    )


@lru_cache(maxsize=1)
def _db():
    import lancedb

    settings = get_settings()
    settings.lancedb_path.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(settings.lancedb_path))


def _table_names(db) -> set[str]:
    """LanceDB 0.39 returns a paginated object from list_tables(); older
    versions returned a plain list. Normalising here keeps the version
    difference in one place. This store holds a single table, so the paging
    token is irrelevant.
    """
    listing = db.list_tables()
    return set(getattr(listing, "tables", listing))


def _table():
    db = _db()
    if TABLE_NAME not in _table_names(db):
        return db.create_table(TABLE_NAME, schema=_schema())
    return db.open_table(TABLE_NAME)


def new_ref() -> str:
    """The id written back to `chunks.embedding_ref`."""
    return str(uuid.uuid4())


def upsert_chunks(rows: list[dict[str, Any]]) -> None:
    """Delete-then-add keyed on chunk_id, so re-ingesting a file cannot leave
    a second vector for the same chunk behind."""
    if not rows:
        return

    table = _table()
    chunk_ids = ",".join(str(int(r["chunk_id"])) for r in rows)
    table.delete(f"chunk_id IN ({chunk_ids})")
    table.add(rows)


def search(
    vector: list[float], *, topic_id: int | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    db = _db()
    if TABLE_NAME not in _table_names(db):
        return []

    query = db.open_table(TABLE_NAME).search(vector).limit(limit)
    if topic_id is not None:
        # prefilter: restrict *before* the ANN search, otherwise a topic with
        # few chunks gets crowded out of the top-k by the rest of the corpus
        # and returns nothing.
        query = query.where(f"topic_id = {int(topic_id)}", prefilter=True)

    results = []
    for row in query.to_list():
        row.pop("vector", None)  # callers never need the raw floats back
        # LanceDB reports squared L2 distance; smaller is closer.
        row["distance"] = row.pop("_distance", None)
        results.append(row)
    return results


def delete_by_source_file(source_file_id: int) -> None:
    db = _db()
    if TABLE_NAME in _table_names(db):
        db.open_table(TABLE_NAME).delete(f"source_file_id = {int(source_file_id)}")


def delete_by_chunk_ids(chunk_ids: list[int]) -> None:
    if not chunk_ids:
        return
    db = _db()
    if TABLE_NAME in _table_names(db):
        ids = ",".join(str(int(i)) for i in chunk_ids)
        db.open_table(TABLE_NAME).delete(f"chunk_id IN ({ids})")


def drop() -> None:
    """Used by tests, and the first half of a rebuild-from-SQLite repair."""
    db = _db()
    if TABLE_NAME in _table_names(db):
        db.drop_table(TABLE_NAME)
