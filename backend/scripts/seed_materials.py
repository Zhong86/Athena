#!/usr/bin/env python
"""Seed the Materials corpus so goal creation can ground on internal data.

    python scripts/seed_materials.py             # seed (refuses if already seeded)
    python scripts/seed_materials.py --reset     # remove the fixtures, then seed
    python scripts/seed_materials.py --clear     # remove the fixtures and stop
    python scripts/seed_materials.py --no-embed  # skip the embedding pass

The whole point is the *embedded* corpus: `personalize_decomposition` decides
between the materials branch and the research branch by running a vector search
per milestone, so SQLite rows alone would leave every milestone ungrounded and
the flow under test would never be exercised. `--no-embed` is therefore only for
checking the relational side without waiting on fastembed's ~130MB first run.

Writes go through the same repository and vector-store functions the real ingest
uses -- the only thing replaced is the tagger, whose topic assignment comes from
the fixture instead of from Hermes, so seeding needs no gateway running.

Honours SQLITE_PATH and friends, so it seeds whichever DB the app is using.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import embeddings  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.materials import repository as repo  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402
from app.materials.ingest.chunker import Chunk, chunk  # noqa: E402
from app.quizzes import repository as quiz_repo  # noqa: E402
from seed_data import DOCUMENTS, FAILED_DOCUMENT, TOPICS  # noqa: E402

# Fixture uploads live in their own directory under uploads_path, so clearing
# them cannot reach a file the user actually uploaded.
SEED_DIRNAME = "seed"

UNDERSTANDING_REASON = "Seeded fixture data, not a measured score"

NO_EMBED_NOTE = (
    "Seeded without embeddings (--no-embed): the chunks are stored but not "
    "searchable, so goal milestones will all take the research branch. Re-run "
    "with --reset to embed them."
)

ALL_FILENAMES = [d["filename"] for d in DOCUMENTS] + [FAILED_DOCUMENT["filename"]]


def _render(doc: dict) -> tuple[str, list[tuple[str, Chunk]]]:
    """Build the document's text and chunk it, one section at a time.

    Chunking per section rather than over the whole file is what lets a chunk
    inherit its section's topic without a tagger: no chunk can straddle two
    sections, so the assignment is exact rather than a guess. Offsets are
    shifted back into whole-file coordinates so `char_start`/`char_end` still
    locate the chunk in `stored_path`, same as a real ingest.
    """
    text = ""
    tagged: list[tuple[str, Chunk]] = []

    for topic, body in doc["sections"]:
        # The heading stays in the chunked text: it is the cheapest topic
        # signal available to an embedding of a paragraph that never names its
        # own subject.
        section = f"## {topic}\n\n{body.strip()}"
        offset = len(text)
        text += section + "\n\n"

        for piece in chunk(section):
            tagged.append(
                (
                    topic,
                    Chunk(
                        text=piece.text,
                        char_start=piece.char_start + offset,
                        char_end=piece.char_end + offset,
                    ),
                )
            )

    return text.rstrip() + "\n", tagged


def _seeded_ids(conn) -> dict[str, int]:
    placeholders = ",".join("?" * len(ALL_FILENAMES))
    return {
        row["filename"]: row["id"]
        for row in conn.execute(
            f"SELECT id, filename FROM source_files WHERE filename IN ({placeholders})",
            ALL_FILENAMES,
        )
    }


def clear() -> int:
    """Remove the fixture files, their vectors, and any topic left with nothing
    behind it.

    A seeded topic is only dropped once no chunk references it, so a topic that
    the user has since attached their own material to survives. Dropping one
    does cascade its understanding_events and quizzes -- acceptable for rows
    this script created, and the reason the match is on the exact fixture names
    rather than anything fuzzier.
    """
    removed = 0
    with connection() as conn:
        for file_id in _seeded_ids(conn).values():
            repo.delete_source_file(conn, file_id)
            # SQL cannot reach LanceDB; the store is derived, so it is purged
            # after the rows it mirrors are gone.
            vector_store.delete_by_source_file(file_id)
            removed += 1

        for name in TOPICS:
            topic = repo.find_topic_by_name(conn, name)
            if topic and repo.count_chunks_for_topic(conn, topic["id"]) == 0:
                conn.execute("DELETE FROM topics WHERE id = ?", (topic["id"],))

    seed_dir = get_settings().uploads_path / SEED_DIRNAME
    for name in ALL_FILENAMES:
        (seed_dir / name).unlink(missing_ok=True)

    return removed


def _embed(file_id: int) -> None:
    """Embed one file's chunks and write the vectors.

    The synchronous twin of `pipeline._embed`, which cannot be reused here: it
    is async and offloads fastembed to a worker thread to keep the request loop
    responsive, neither of which a one-shot script needs. The record shape below
    is the one thing that must track `vectors._schema()` -- Lance rejects the
    write outright if it drifts, so the failure is loud.
    """
    with connection() as conn:
        rows = repo.chunks_for_source_file(conn, file_id)
    if not rows:
        return

    vectors = embeddings.embed_passages([r["text"] for r in rows])

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

    # Vectors first, then the refs in SQLite -- same order as the pipeline, so a
    # crash between them leaves a retryable NULL ref rather than a ref pointing
    # at a vector that was never written.
    vector_store.upsert_chunks(records)
    with connection() as conn:
        repo.set_embedding_refs(conn, refs)


def seed(*, embed: bool = True) -> None:
    seed_dir = get_settings().uploads_path / SEED_DIRNAME
    seed_dir.mkdir(parents=True, exist_ok=True)

    with connection() as conn:
        topic_ids: dict[str, int] = {}
        for name, (description, _) in TOPICS.items():
            existing = repo.find_topic_by_name(conn, name)
            topic_ids[name] = (
                existing
                or repo.create_topic(conn, name=name, description=description)
            )["id"]

    file_ids: list[int] = []
    for doc in DOCUMENTS:
        text, tagged = _render(doc)
        path = seed_dir / doc["filename"]
        path.write_text(text, encoding="utf-8")

        with connection() as conn:
            source_file = repo.create_source_file(
                conn,
                filename=doc["filename"],
                upload_type=doc["upload_type"],
                byte_size=len(text.encode("utf-8")),
                # A Drive-origin row keeps no bytes on the VPS, so it gets no
                # stored_path even though this fixture wrote the text out.
                stored_path=None if doc["origin"] == "drive" else str(path),
                origin=doc["origin"],
                drive_file_id=doc.get("drive_file_id"),
                drive_url=doc.get("drive_url"),
                drive_modified_at=doc.get("drive_modified_at"),
            )
            file_id = source_file["id"]
            chunk_ids = repo.replace_chunks(conn, file_id, [c for _, c in tagged])
            repo.assign_topics(
                conn,
                {
                    chunk_id: topic_ids[topic]
                    for chunk_id, (topic, _) in zip(chunk_ids, tagged)
                },
            )

        file_ids.append(file_id)
        print(f"  {doc['filename']:<32} {len(chunk_ids):>3} chunks  ({doc['origin']})")

    with connection() as conn:
        failed = repo.create_source_file(
            conn,
            filename=FAILED_DOCUMENT["filename"],
            upload_type=FAILED_DOCUMENT["upload_type"],
            byte_size=FAILED_DOCUMENT["byte_size"],
            stored_path=None,
            origin=FAILED_DOCUMENT["origin"],
        )
        repo.set_status(conn, failed["id"], "failed", error=FAILED_DOCUMENT["error"])
    print(f"  {FAILED_DOCUMENT['filename']:<32}   0 chunks  (failed)")

    # Scores go through the quizzes repository so each one lands with the
    # understanding_event that explains it -- a bare number on the topic row
    # would be indistinguishable from a measured one.
    with connection() as conn:
        for name, (_, understanding) in TOPICS.items():
            if understanding < 0:
                continue  # -1 is the column default: no signal, nothing to log
            quiz_repo.apply_understanding(
                conn,
                topic_id=topic_ids[name],
                understanding=understanding,
                previous=-1,
                source="manual",
                reason=UNDERSTANDING_REASON,
                evidence={"seed": True, "script": "scripts/seed_materials.py"},
            )

    if not embed:
        # 'ready' with a note, not 'pending': the chunks really are stored, and
        # a row left pending would look like an ingest that never finished. The
        # note is what keeps "ready" from implying searchable.
        with connection() as conn:
            for file_id in file_ids:
                repo.set_status(conn, file_id, "ready", error=NO_EMBED_NOTE)
        print(f"\n{NO_EMBED_NOTE}")
        return

    print("\nembedding (first run downloads the model, ~130MB)...")
    for file_id in file_ids:
        _embed(file_id)
        with connection() as conn:
            repo.set_status(conn, file_id, "ready")
    print("embedded.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset", action="store_true", help="remove the fixtures first, then seed"
    )
    parser.add_argument(
        "--clear", action="store_true", help="remove the fixtures and exit"
    )
    parser.add_argument(
        "--no-embed", action="store_true", help="skip the embedding pass"
    )
    args = parser.parse_args()

    init_db()

    if args.clear or args.reset:
        print(f"cleared {clear()} fixture files")
        if args.clear:
            return

    with connection() as conn:
        already = _seeded_ids(conn)
    if already:
        print(
            f"already seeded ({len(already)} fixture files present). "
            "Re-run with --reset to replace them."
        )
        raise SystemExit(1)

    print(f"seeding {get_settings().sqlite_path}")
    seed(embed=not args.no_embed)

    with connection() as conn:
        topics = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"\ndone: {topics} topics, {chunks} chunks")


if __name__ == "__main__":
    main()

