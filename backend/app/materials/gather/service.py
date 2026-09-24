"""The gather cycle itself, shared by every trigger.

`POST /materials/gather/run` (cron, the frontend's "Sync now") and the
`gather_materials` MCP tool (Hermes, asked from a chat session) both need
exactly the same thing: run the graph, record the run and its Knowledge-Sync
session, hand back what was found. What differs between callers is auth (the
HTTP route is reachable from outside the process and needs the shared
X-Gather-Token; the MCP tool is already behind the /mcp bearer token, so it
doesn't check a second secret) and how the resulting ingests get scheduled
(FastAPI's `BackgroundTasks` for the HTTP route; a caller with no
`BackgroundTasks` object -- like the MCP tool -- has to schedule its own).
That scheduling step is deliberately left to the caller rather than done
here, so this module has no FastAPI dependency at all.
"""

import logging

import anyio.to_thread

from app.db import connection
from app.materials import repository as materials_repo
from app.materials.gather import graph as gather_graph
from app.materials.gather import repository as repo
from app.materials.gather.state import new_state, result_summary
from app.sessions import repository as sessions_repo

# Tags a gather run's `sessions.payload` distinctly from any other 'cron'
# session (e.g. a future Hermes-driven finding) so the run detail page knows
# which view to render, and so GET /runs/{id} knows what it's allowed to open.
PAYLOAD_KIND = "materials_gather"

log = logging.getLogger(__name__)


def _run_summary_text(summary: dict, folder: dict | None) -> str:
    scope = f" (Drive scoped to “{folder['folder_name']}” and its subfolders)" if folder else ""

    # The common case on a healthy, frequently-run schedule: nothing has
    # changed in the inbox or Drive since the cursor from the last run. That
    # reads as "0 imported, 0 refreshed, 0 skipped of 0 candidates" without
    # this -- indistinguishable from something being broken.
    if summary["candidates_seen"] == 0:
        return f"Nothing new since the last sync{scope}"

    parts = [
        f"{len(summary['imported_file_ids'])} imported",
        f"{len(summary['refreshed_file_ids'])} refreshed",
        f"{len(summary['skipped_local'])} skipped",
    ]
    return f"{', '.join(parts)} of {summary['candidates_seen']} candidates{scope}"


async def run_gather_cycle() -> dict:
    """Runs one full scan -> decide -> apply cycle and records it.

    Returns `{run_id, session_id, candidates_seen, imported_file_ids,
    refreshed_file_ids, skipped_local}`. The caller is responsible for
    actually triggering `app.materials.ingest.pipeline.ingest()` on the
    imported/refreshed ids -- this function never touches ingestion, since
    *how* to schedule it (BackgroundTasks vs. a bare task) is the one thing
    that legitimately differs per caller.
    """
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
        folder = repo.get_drive_folder(conn)

        # Filenames captured now, while the rows definitely still exist --
        # not just ids. If one of these files is later deleted from Materials
        # (an ordinary thing to do), GET /runs/{id} still has a name to show
        # for it under "removed" instead of the file just vanishing from the
        # run's history as if it never happened.
        touched_ids = [*summary["imported_file_ids"], *summary["refreshed_file_ids"]]
        touched = []
        for file_id in touched_ids:
            source_file = materials_repo.get_source_file(conn, file_id)
            if source_file:
                touched.append({"file_id": file_id, "filename": source_file["filename"]})

        # A gather run that never writes a `sessions` row is invisible on
        # Knowledge-Sync forever -- see sessions/repository.py's own docstring
        # on that being the point of the table. Which topics these files
        # landed in is not stored here: tagging runs in `ingest()` *after*
        # this returns, so nothing is tagged yet at write time. GET
        # /runs/{id} resolves topics fresh from current state instead --
        # see GatherRunMaterials's docstring.
        session = sessions_repo.create(
            conn,
            type="cron",
            payload={
                "kind": PAYLOAD_KIND,
                "touched": touched,
                "skipped_local": summary["skipped_local"],
            },
            summary=_run_summary_text(summary, folder),
        )
        sessions_repo.update(conn, session["id"], title="Materials gather")

    log.info(
        "gather run %s: %s candidates, %s imported, %s refreshed, %s skipped",
        run["id"],
        summary["candidates_seen"],
        len(summary["imported_file_ids"]),
        len(summary["refreshed_file_ids"]),
        len(summary["skipped_local"]),
    )

    return {"run_id": run["id"], "session_id": session["id"], **summary}
