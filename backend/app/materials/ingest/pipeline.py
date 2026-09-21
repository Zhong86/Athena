"""Orchestrates extract -> chunk -> tag -> embed, and owns `ingest_status`.

Nothing else writes that column. Each stage opens its own short-lived
connection and commits: a five-minute ingest must not hold a write transaction
open throughout, and a crash partway leaves the file in a readable
non-`ready` state instead of silently rolling all the work back.

Chunks land in SQLite at the end of extraction -- before tagging or embedding --
so the row count is visible immediately and a later failure still leaves
something to retry from.
"""

import logging
import sqlite3
from pathlib import Path

import anyio.to_thread

from agent import embeddings
from app.db import connection
from app.materials import repository as repo
from app.materials import vectors as vector_store
from app.materials.ingest import tagger
from app.materials.ingest.chunker import chunk
from app.materials.ingest.extract import ExtractError, extract

log = logging.getLogger(__name__)


class IngestError(RuntimeError):
    pass


def _claim(file_id: int) -> dict | None:
    """Move the file into `extracting`, or refuse.

    Guards against a double-submit: only `pending` and `failed` may start a
    run, so a second request while one is in flight cannot duplicate chunks.
    """
    with connection() as conn:
        source_file = repo.get_source_file(conn, file_id)
        if source_file is None:
            return None
        if source_file["ingest_status"] not in repo.RESTARTABLE:
            return None
        repo.set_status(conn, file_id, "extracting")
        return source_file


def _resolve_topics(refs: list[int | None], proposed: list) -> dict[int, int | None]:
    """Create each proposed topic once and map its placeholder ref to a row id."""
    ref_to_id: dict[int, int] = {}
    with connection() as conn:
        for proposal in proposed:
            try:
                created = repo.create_topic(
                    conn, name=proposal.name, description=proposal.description
                )
                ref_to_id[proposal.ref] = created["id"]
            except sqlite3.IntegrityError:
                # topics.name is UNIQUE. The tagger dedupes case-insensitively
                # against what it was shown, but a topic created since that
                # snapshot can still collide -- reuse it rather than failing.
                existing = repo.find_topic_by_name(conn, proposal.name)
                if existing:
                    ref_to_id[proposal.ref] = existing["id"]

    return {
        i: (ref_to_id.get(ref) if ref is not None and ref < 0 else ref)
        for i, ref in enumerate(refs)
    }


async def _tag(file_id: int, chunk_ids: list[int], texts: list[str]) -> None:
    with connection() as conn:
        repo.set_status(conn, file_id, "tagging")
        existing = repo.list_topics_for_prompt(conn)

    result = await tagger.assign_topics(texts, existing)
    by_position = _resolve_topics(result.refs, result.proposed)

    with connection() as conn:
        repo.assign_topics(
            conn,
            {chunk_id: by_position.get(i) for i, chunk_id in enumerate(chunk_ids)},
        )


async def _embed(file_id: int) -> None:
    with connection() as conn:
        repo.set_status(conn, file_id, "embedding")
        rows = repo.chunks_for_source_file(conn, file_id)
    if not rows:
        return

    # fastembed is synchronous and CPU-bound; running it inline would stall the
    # event loop for every other request for the duration of the upload.
    vectors = await anyio.to_thread.run_sync(
        embeddings.embed_passages, [r["text"] for r in rows]
    )

    records, refs = [], {}
    for row, vector in zip(rows, vectors):
        ref = vector_store.new_ref()
        refs[row["id"]] = ref
        records.append(
            {
                "embedding_ref": ref,
                "chunk_id": row["id"],
                "topic_id": row["topic_id"],
                "source_file_id": file_id,
                "text": row["text"],
                "vector": vector,
            }
        )

    # Vectors first, then the refs in SQLite. The reverse order could leave a
    # chunk claiming an embedding_ref that was never written.
    await anyio.to_thread.run_sync(vector_store.upsert_chunks, records)
    with connection() as conn:
        repo.set_embedding_refs(conn, refs)


async def ingest(file_id: int) -> None:
    """Run the full pipeline. Never raises -- failures are recorded on the row,
    because this runs detached as a background task with no caller to catch."""
    source_file = _claim(file_id)
    if source_file is None:
        return

    try:
        stored_path = source_file["stored_path"]
        if not stored_path or not Path(stored_path).exists():
            raise IngestError("the uploaded file is no longer on disk")

        text = extract(Path(stored_path).read_bytes(), source_file["upload_type"])
        chunks = chunk(text)
        if not chunks:
            raise IngestError("no usable text found in this file")

        with connection() as conn:
            chunk_ids = repo.replace_chunks(conn, file_id, chunks)

        await _tag(file_id, chunk_ids, [c.text for c in chunks])
        await _embed(file_id)

        with connection() as conn:
            repo.set_status(conn, file_id, "ready")

    except (ExtractError, IngestError) as exc:
        _fail(file_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - background task, nothing above us
        log.exception("ingest failed for source_file %s", file_id)
        _fail(file_id, f"unexpected error during ingestion: {exc}")


def _fail(file_id: int, message: str) -> None:
    try:
        with connection() as conn:
            repo.set_status(conn, file_id, "failed", error=message)
    except Exception:  # pragma: no cover - the DB itself is gone
        log.exception("could not record ingest failure for %s", file_id)
