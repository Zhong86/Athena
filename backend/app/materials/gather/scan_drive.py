"""List what's new (or changed) in Drive since the last complete scan.

Sync node -- see `app.goals.llm`'s docstring for why: LangGraph's node
functions are sync throughout this codebase so the whole graph can be
dispatched with `anyio.to_thread.run_sync` from the router, which is what lets
`_run` below bridge back to the async Drive client with `anyio.from_thread.run`
when there's a surrounding event loop, and a bare `anyio.run` when there isn't
(a test calling the graph directly).
"""

import logging

import anyio
import anyio.from_thread

from app.config import get_settings
from app.connections import google_drive
from app.db import connection
from app.materials import drive
from app.materials import repository as repo
from app.materials.gather import repository as gather_repo
from app.materials.gather.state import DriveCandidate, GatherState

log = logging.getLogger(__name__)


def _run(factory):
    try:
        return anyio.from_thread.run(factory)
    except (google_drive.DriveError, google_drive.DriveNotConnected):
        raise
    except RuntimeError:
        return anyio.run(factory)


def _subfolder_ids(token: str, root_id: str, cap: int) -> list[str]:
    """Breadth-first walk of the folder tree rooted at `root_id`, returning
    the root plus every descendant folder id.

    Drive's API has no recursive "everything under this folder" query --
    `'X' in parents` only matches direct children -- so a scoped scan has to
    discover the tree itself, one `list_files` call per folder. `cap` bounds
    total folders visited (not files), independent of
    `materials_gather_drive_scan_cap`, so a folder scope pointed at a huge
    tree can't turn one run into hundreds of Drive calls.
    """
    ids = [root_id]
    frontier = [root_id]

    while frontier and len(ids) < cap:
        parent_id = frontier.pop(0)
        query = (
            f"trashed = false and mimeType = '{drive.FOLDER_MIME}' "
            f"and '{parent_id}' in parents"
        )
        page_token: str | None = None
        while True:
            page = _run(
                lambda: google_drive.list_files(token, query=query, page_token=page_token)
            )
            for child in page.get("files", []):
                if len(ids) >= cap:
                    break
                ids.append(child["id"])
                frontier.append(child["id"])

            page_token = page.get("nextPageToken")
            if not page_token or len(ids) >= cap:
                break

    return ids


def scan_drive(state: GatherState) -> dict:
    settings = get_settings()

    try:
        token = _run(lambda: google_drive.access_token())
    except google_drive.DriveNotConnected:
        # Not an error: gather is meant to work from the local inbox alone
        # when nothing is connected. Nothing was actually scanned, though, so
        # the cursor must not move.
        return {"drive_new": [], "drive_refresh": [], "drive_incomplete": True}
    except google_drive.DriveError as exc:
        log.warning("gather: could not reach Drive: %s", exc)
        return {"drive_new": [], "drive_refresh": [], "drive_incomplete": True}

    with connection() as conn:
        folder = gather_repo.get_drive_folder(conn)

    raw_files: list[dict] = []
    incomplete = False

    try:
        # No scope -> one pass with folder_id=None, matching all of Drive.
        # Scoped -> one pass per folder in the tree (root + every subfolder).
        folder_ids = (
            _subfolder_ids(
                token, folder["folder_id"], settings.materials_gather_drive_folder_cap
            )
            if folder
            else [None]
        )

        for folder_id in folder_ids:
            query = drive.build_query(
                modified_after=state.get("drive_cursor"), folder_id=folder_id
            )
            page_token: str | None = None
            while True:
                page = _run(
                    lambda: google_drive.list_files(
                        token, query=query, page_token=page_token
                    )
                )
                raw_files.extend(page.get("files", []))
                if len(raw_files) >= settings.materials_gather_drive_scan_cap:
                    incomplete = True
                    break

                page_token = page.get("nextPageToken")
                if not page_token:
                    break

            if incomplete:
                break
    except google_drive.DriveError as exc:
        log.warning("gather: Drive listing failed mid-scan: %s", exc)
        incomplete = True

    mapped = [
        f for f in (drive.as_drive_file(r) for r in raw_files) if f is not None
    ]

    with connection() as conn:
        known = repo.source_ids_by_drive_id(conn, [f["drive_file_id"] for f in mapped])

    drive_new: list[DriveCandidate] = []
    drive_refresh: list[DriveCandidate] = []
    for f in mapped:
        source_file_id = known.get(f["drive_file_id"])
        f["source_file_id"] = source_file_id
        (drive_refresh if source_file_id else drive_new).append(f)

    return {
        "drive_new": drive_new,
        "drive_refresh": drive_refresh,
        "drive_incomplete": incomplete,
    }
