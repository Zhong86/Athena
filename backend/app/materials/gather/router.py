"""HTTP surface for the gather graph -- meant to be hit by cron, not a browser.

One endpoint, no polling: unlike an upload, a gather run doesn't have a
student waiting on it, so there's no `source_file_id` to poll for -- the
response already carries what happened.
"""

import logging

import anyio.to_thread
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.config import get_settings
from app.db import connection
from app.materials.gather import graph as gather_graph
from app.materials.gather import repository as repo
from app.materials.gather.schemas import GatherRunResult
from app.materials.gather.state import new_state, result_summary
from app.materials.ingest.pipeline import ingest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/materials/gather", tags=["materials"])


def _check_token(token: str | None) -> None:
    configured = get_settings().materials_gather_token
    if not configured:
        raise HTTPException(503, "materials_gather_token is not configured")
    if token != configured:
        raise HTTPException(403, "missing or incorrect X-Gather-Token")


@router.post("/run", response_model=GatherRunResult)
async def run_gather(
    tasks: BackgroundTasks, x_gather_token: str | None = Header(default=None)
) -> GatherRunResult:
    _check_token(x_gather_token)

    with connection() as conn:
        run = repo.start_run(conn)

    state = new_state(run["id"], run["drive_cursor"])
    # Same dispatch as app.goals.router._invoke: the whole (sync) graph runs
    # in a worker thread so its nodes can bridge back to async Hermes/Drive
    # calls with anyio.from_thread.run.
    result = await anyio.to_thread.run_sync(lambda: gather_graph.compiled().invoke(state))
    summary = result_summary(result)

    # Only a Drive scan that actually completed earns the cursor advance --
    # not connected, a call failure, or the page cap all leave it where it
    # was, so the next run re-scans the same window instead of silently
    # treating unseen files as seen.
    next_cursor = (
        run["drive_cursor"] if result.get("drive_incomplete") else run["started_at"]
    )

    with connection() as conn:
        repo.finish_run(
            conn,
            run["id"],
            drive_cursor=next_cursor,
            candidates_seen=summary["candidates_seen"],
            imported_count=len(summary["imported_file_ids"]),
            skipped_count=len(summary["skipped_local"]),
            error=result.get("relevance_error"),
        )

    for file_id in [*summary["imported_file_ids"], *summary["refreshed_file_ids"]]:
        tasks.add_task(ingest, file_id)

    log.info(
        "gather run %s: %s candidates, %s imported, %s refreshed, %s skipped",
        run["id"],
        summary["candidates_seen"],
        len(summary["imported_file_ids"]),
        len(summary["refreshed_file_ids"]),
        len(summary["skipped_local"]),
    )

    return GatherRunResult(run_id=run["id"], **summary)
