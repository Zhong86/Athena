"""`search_materials(topic, query)` -- the signature named in the spec.

Reused by Step 4's quiz generation and Step 6's `personalize_decomposition`, so
the Python function is the real artefact; the HTTP endpoint and the Hermes tool
are both thin wrappers over it.

`topic` is a *name*, not an id, because the caller is a language model working
from a topic list it saw in a prompt.
"""

from typing import Any

from agent import embeddings
from app.db import connection
from app.materials import repository as repo
from app.materials import vectors as vector_store


def search_materials(
    topic: str | None, query: str, *, limit: int = 5
) -> dict[str, Any]:
    """Returns the hits plus how the topic filter was resolved.

    An unrecognised topic searches everything rather than returning nothing: a
    model that guesses "Thermo" when the topic is "Thermodynamics" should still
    get useful material, and `topic_resolved` tells the caller what happened so
    it can say so instead of silently implying a filter was applied.
    """
    if not query.strip():
        return {"topic_resolved": None, "topic_matched": False, "results": []}

    topic_id: int | None = None
    topic_name: str | None = None
    if topic:
        with connection() as conn:
            matched = repo.find_topic_by_name(conn, topic)
        if matched:
            topic_id, topic_name = matched["id"], matched["name"]

    hits = vector_store.search(
        embeddings.embed_query(query), topic_id=topic_id, limit=limit
    )
    if not hits:
        return {
            "topic_resolved": topic_name,
            "topic_matched": topic_id is not None,
            "results": [],
        }

    # Names live in SQLite, not Lance -- hydrate every hit in one round-trip.
    with connection() as conn:
        meta = repo.get_chunks(conn, [h["chunk_id"] for h in hits])

    results = []
    for hit in hits:
        row = meta.get(hit["chunk_id"])
        if row is None:
            # A vector whose SQLite row is gone: the store is derived, so trust
            # SQLite and skip it rather than returning a dangling result.
            continue
        results.append(
            {
                "chunk_id": row["id"],
                "text": row["text"],
                "topic_id": row["topic_id"],
                "topic_name": row["topic_name"],
                "source_file_id": row["source_file_id"],
                "source_filename": row["source_filename"],
                "distance": hit.get("distance"),
            }
        )

    return {
        "topic_resolved": topic_name,
        "topic_matched": topic_id is not None,
        "results": results,
    }
