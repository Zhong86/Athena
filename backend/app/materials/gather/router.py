"""HTTP surface for the gather graph -- meant to be hit by cron, not a browser.

One endpoint, no polling: unlike an upload, a gather run doesn't have a
student waiting on it, so there's no `source_file_id` to poll for -- the
response already carries what happened.
"""

import json
import logging
import shutil

import anyio.to_thread
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.config import get_settings
from app.connections import google_drive
from app.db import connection
from app.materials import drive
from app.materials import repository as materials_repo
from app.materials import vectors as vector_store
from app.materials.gather import repository as repo
from app.materials.gather import service
from app.materials.gather.schemas import (
    GatherFolderConfig,
    GatherFolderUpdate,
    GatherPendingFile,
    GatherRunMaterials,
    GatherRunResult,
    GatherTopic,
    TestModeStatus,
)
from app.materials.gather.service import PAYLOAD_KIND
from app.materials.ingest.pipeline import ingest
from app.sessions import repository as sessions_repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/materials/gather", tags=["materials"])


def _check_token(token: str | None) -> None:
    configured = get_settings().materials_gather_token
    if not configured:
        raise HTTPException(503, "materials_gather_token is not configured")
    if token != configured:
        raise HTTPException(403, "missing or incorrect X-Gather-Token")


@router.get("/config", response_model=GatherFolderConfig)
def get_config() -> GatherFolderConfig:
    with connection() as conn:
        folder = repo.get_drive_folder(conn)
    return GatherFolderConfig(**folder) if folder else GatherFolderConfig()


@router.put("/config", response_model=GatherFolderConfig)
async def set_config(body: GatherFolderUpdate) -> GatherFolderConfig:
    """Resolves the pasted link/id against Drive before storing it -- a typo'd
    id would otherwise silently scope every future scan to nothing."""
    try:
        folder_id = drive.extract_folder_id(body.folder)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        token = await google_drive.access_token()
        meta = await google_drive.get_file(token, folder_id)
    except google_drive.DriveNotConnected as exc:
        raise HTTPException(409, str(exc)) from exc
    except google_drive.DriveError as exc:
        raise HTTPException(502, str(exc)) from exc

    if meta.get("mimeType") != drive.FOLDER_MIME:
        raise HTTPException(
            400, f"{meta.get('name', folder_id)!r} is not a Drive folder."
        )

    with connection() as conn:
        repo.set_drive_folder(conn, folder_id=folder_id, folder_name=meta["name"])

    return GatherFolderConfig(folder_id=folder_id, folder_name=meta["name"])


@router.delete("/config", response_model=GatherFolderConfig)
def clear_config() -> GatherFolderConfig:
    with connection() as conn:
        repo.clear_drive_folder(conn)
    return GatherFolderConfig()


@router.post("/run", response_model=GatherRunResult)
async def run_gather(
    tasks: BackgroundTasks, x_gather_token: str | None = Header(default=None)
) -> GatherRunResult:
    _check_token(x_gather_token)

    result = await service.run_gather_cycle()

    for file_id in [*result["imported_file_ids"], *result["refreshed_file_ids"]]:
        tasks.add_task(ingest, file_id)

    return GatherRunResult(**result)


@router.get("/runs/{session_id}", response_model=GatherRunMaterials)
def get_run_materials(session_id: int) -> GatherRunMaterials:
    """What this run actually added, grouped by topic rather than by file --
    see `GatherRunMaterials`'s docstring for why this is computed here rather
    than read back from the session payload."""
    with connection() as conn:
        session = sessions_repo.get(conn, session_id)
        payload = (session or {}).get("payload") or {}
        if session is None or payload.get("kind") != PAYLOAD_KIND:
            raise HTTPException(404, f"gather run {session_id} not found")

        touched = payload.get("touched", [])
        touched_ids = [t["file_id"] for t in touched]
        filename_at_run_time = {t["file_id"]: t["filename"] for t in touched}

        topic_rows = materials_repo.topics_for_source_files(conn, touched_ids)
        files_by_id = materials_repo.list_source_files_by_ids(conn, touched_ids)

    tagged_ids = {f["source_file_id"] for t in topic_rows for f in t["files"]}
    pending = []
    removed = []
    for file_id in touched_ids:
        if file_id in tagged_ids:
            continue
        source_file = files_by_id.get(file_id)
        if source_file is None:
            # Imported (or refreshed) by this run, deleted from Materials
            # since -- still a fact about what the run did, so it's named
            # rather than silently dropped from the view.
            removed.append(filename_at_run_time.get(file_id, f"file {file_id}"))
            continue
        pending.append(
            GatherPendingFile(
                source_file_id=file_id,
                filename=source_file["filename"],
                upload_type=source_file["upload_type"],
                origin=source_file["origin"],
                drive_url=source_file["drive_url"],
                ingest_status=source_file["ingest_status"],
            )
        )

    return GatherRunMaterials(
        session_id=session_id,
        topics=[GatherTopic(**t) for t in topic_rows],
        pending=pending,
        removed=removed,
        skipped=payload.get("skipped_local", []),
    )


@router.get("/test-mode", response_model=TestModeStatus)
def get_test_mode() -> TestModeStatus:
    """Lets the frontend decide whether to render the reset button at all,
    without duplicating the TEST_MODE flag into its own env config."""
    return TestModeStatus(enabled=get_settings().test_mode)


@router.post("/reset", status_code=204)
async def reset_materials() -> None:
    """Dev-only: wipes every materials + gather table back to empty, for
    re-running the gather flow from a clean slate while testing.

    404s outside TEST_MODE -- not 403 -- so a bulk-delete endpoint's very
    existence isn't revealed to anything that isn't deliberately opted in.
    Goals, quizzes, chats and connections are untouched; this is materials
    and the sync activity gather produced, nothing else.
    """
    if not get_settings().test_mode:
        raise HTTPException(404)

    with connection() as conn:
        # chunks cascade off source_files (ON DELETE CASCADE); chunks.topic_id
        # would SET NULL on a topics delete, but source_files goes first so
        # there's nothing left to null by the time topics does.
        conn.execute("DELETE FROM source_files")
        conn.execute("DELETE FROM topics")
        conn.execute("DELETE FROM materials_gather_runs")
        repo.clear_drive_folder(conn)

        # Only the sessions gather itself created -- a plain `type = 'cron'`
        # delete would also take any other kind of unprompted finding sharing
        # that type. json.loads in Python rather than SQLite's json_extract:
        # the JSON1 extension isn't guaranteed compiled in.
        gather_session_ids = [
            row["id"]
            for row in conn.execute("SELECT id, payload FROM sessions WHERE type = 'cron'")
            if row["payload"] and json.loads(row["payload"]).get("kind") == PAYLOAD_KIND
        ]
        if gather_session_ids:
            placeholders = ",".join("?" * len(gather_session_ids))
            conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})", gather_session_ids
            )

    await anyio.to_thread.run_sync(vector_store.drop)

    uploads_root = get_settings().uploads_path
    if uploads_root.exists():
        shutil.rmtree(uploads_root)
    uploads_root.mkdir(parents=True, exist_ok=True)

    log.warning("materials reset: all materials + gather state wiped (TEST_MODE)")
