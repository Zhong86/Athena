"""SQL for `materials_gather_runs` and the gather graph's row in `settings`.

`materials_gather_runs` is kept separate from `app.connections.repository`'s
`last_synced_at`: that column is stamped by every ordinary Drive fetch during
ingest (see `materials/ingest/pipeline.py`), so reusing it here would let an
unrelated manual import silently advance the gather cursor.

The Drive folder scope lives in the generic `settings` table (`001_initial.sql`)
rather than a bespoke column or table: it is exactly the "page-scoped,
JSON-encoded" shape that table exists for, and it is the one piece of gather
config a user edits at runtime through the UI -- unlike the `materials_gather_*`
`Settings` fields in `app/config.py`, which are env-configured and need a
restart to change.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clock import utc_now_iso
from app.config import get_settings

DRIVE_FOLDER_KEY = "materials.gather_drive_folder"


def get_drive_folder(conn: sqlite3.Connection) -> dict[str, str] | None:
    """`{"folder_id", "folder_name"}`, or None when gather is scoped to all of
    Drive (the default -- no row means no restriction, not an error)."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (DRIVE_FOLDER_KEY,)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def set_drive_folder(conn: sqlite3.Connection, *, folder_id: str, folder_name: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (DRIVE_FOLDER_KEY, json.dumps({"folder_id": folder_id, "folder_name": folder_name})),
    )


def clear_drive_folder(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM settings WHERE key = ?", (DRIVE_FOLDER_KEY,))


def _last_cursor(conn: sqlite3.Connection) -> str | None:
    """The cursor of the most recent *complete* run, or None if there has
    never been one. Only a finished run's cursor counts -- a run left open by
    a crash mid-scan must not be trusted as a boundary."""
    row = conn.execute(
        """
        SELECT drive_cursor FROM materials_gather_runs
        WHERE finished_at IS NOT NULL
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    return row["drive_cursor"] if row else None


def start_run(conn: sqlite3.Connection) -> dict[str, Any]:
    """Open a new run row and resolve the Drive cursor to scan from.

    First-ever run (no prior finished row) falls back to
    `materials_gather_lookback_days` ago rather than scanning all of Drive.
    """
    cursor = _last_cursor(conn)
    if cursor is None:
        lookback = get_settings().materials_gather_lookback_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback)
        cursor = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    row = conn.execute(
        "INSERT INTO materials_gather_runs DEFAULT VALUES RETURNING *"
    ).fetchone()
    run = dict(row)
    # The row's own `drive_cursor` column stays NULL until `finish_run` --
    # this is the resolved boundary the caller queries Drive with.
    run["drive_cursor"] = cursor
    return run


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    drive_cursor: str | None,
    candidates_seen: int,
    imported_count: int,
    skipped_count: int,
    error: str | None = None,
) -> None:
    """`drive_cursor` is the value to persist going forward -- the caller
    decides whether that's this run's start time (a complete scan) or the
    same cursor it started from (the scan cap was hit, or Hermes never
    answered, so nothing should be considered "seen" yet)."""
    conn.execute(
        """
        UPDATE materials_gather_runs
           SET finished_at     = ?,
               drive_cursor    = ?,
               candidates_seen = ?,
               imported_count  = ?,
               skipped_count   = ?,
               error           = ?
         WHERE id = ?
        """,
        (
            utc_now_iso(),
            drive_cursor,
            candidates_seen,
            imported_count,
            skipped_count,
            error,
            run_id,
        ),
    )
