"""Turn a relevance decision into `source_files` rows.

This is the only node that writes anything -- scan_inbox/scan_drive/
decide_relevance are all read-only. It never calls `ingest()` itself
(no `BackgroundTasks` inside a graph node); the router does that with the
`imported_file_ids`/`refreshed_file_ids` this returns, the same call the
manual upload and Drive-import endpoints already make.
"""

import logging
import shutil
import sqlite3
from pathlib import Path

from app.config import get_settings
from app.db import connection
from app.materials import repository as repo
from app.materials.gather.scan_inbox import SKIPPED_DIRNAME
from app.materials.gather.state import GatherState

log = logging.getLogger(__name__)


def _skip_path(inbox: Path, name: str) -> Path:
    """Where a rejected file lands in `.skipped/`, suffixed on collision so a
    second run's reject of a same-named file doesn't clobber the first."""
    skipped_dir = inbox / SKIPPED_DIRNAME
    skipped_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    target = skipped_dir / name
    n = 1
    while target.exists():
        target = skipped_dir / f"{stem}-{n}{suffix}"
        n += 1
    return target


def _import_local(conn: sqlite3.Connection, candidate: dict) -> int:
    """Same shape as `materials/router.py`'s `_accept`: create the row, then
    move the bytes, then record where they landed -- so a crash between the
    two leaves a `pending` row an operator can inspect rather than losing the
    file's existence entirely."""
    source_file = repo.create_source_file(
        conn,
        filename=candidate["name"],
        upload_type=candidate["upload_type"],
        byte_size=candidate["size"],
    )
    file_id = source_file["id"]

    directory = get_settings().uploads_path / str(file_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(candidate["name"]).name
    shutil.move(candidate["path"], str(target))

    conn.execute(
        "UPDATE source_files SET stored_path = ? WHERE id = ?", (str(target), file_id)
    )
    return file_id


def _import_drive(conn: sqlite3.Connection, candidate: dict) -> int | None:
    try:
        row = repo.create_source_file(
            conn,
            filename=candidate["name"],
            upload_type=candidate["upload_type"],
            origin="drive",
            drive_file_id=candidate["drive_file_id"],
            drive_url=candidate.get("web_view_link"),
            drive_modified_at=candidate.get("modified_at"),
            drive_mime_type=candidate.get("mime_type"),
        )
    except sqlite3.IntegrityError:
        # Imported through another path (e.g. the manual picker) between this
        # run's scan and now -- not this run's file to claim.
        log.info("gather: %s was imported elsewhere mid-run, skipping", candidate["name"])
        return None
    return row["id"]


def _refresh_drive(conn: sqlite3.Connection, candidate: dict) -> int | None:
    row = repo.refresh_drive_pointer(
        conn,
        candidate["source_file_id"],
        filename=candidate["name"],
        upload_type=candidate["upload_type"],
        drive_url=candidate.get("web_view_link"),
        drive_modified_at=candidate.get("modified_at"),
        drive_mime_type=candidate.get("mime_type"),
    )
    return row["id"] if row else None


def apply_decisions(state: GatherState) -> dict:
    inbox = get_settings().materials_inbox_path
    selected_local = set(state.get("selected_local") or [])
    selected_drive = set(state.get("selected_drive") or [])

    imported_file_ids: list[int] = []
    for candidate in state.get("local_candidates") or []:
        if candidate["path"] not in selected_local:
            continue
        with connection() as conn:
            imported_file_ids.append(_import_local(conn, candidate))

    for candidate in state.get("drive_new") or []:
        if candidate["drive_file_id"] not in selected_drive:
            continue
        with connection() as conn:
            file_id = _import_drive(conn, candidate)
        if file_id is not None:
            imported_file_ids.append(file_id)

    refreshed_file_ids: list[int] = []
    for candidate in state.get("drive_refresh") or []:
        with connection() as conn:
            file_id = _refresh_drive(conn, candidate)
        if file_id is not None:
            refreshed_file_ids.append(file_id)

    skipped_local: list[str] = []
    for candidate in state.get("local_candidates") or []:
        if candidate["path"] in selected_local:
            continue
        source = Path(candidate["path"])
        if not source.exists():
            continue  # already moved by a concurrent run
        target = _skip_path(inbox, candidate["name"])
        shutil.move(str(source), str(target))
        skipped_local.append(target.name)

    return {
        "imported_file_ids": imported_file_ids,
        "refreshed_file_ids": refreshed_file_ids,
        "skipped_local": skipped_local,
    }
