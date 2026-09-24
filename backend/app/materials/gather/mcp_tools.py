"""Hermes-facing tool functions over the gather graph.

Plain callables, not decorated -- same reasoning as `materials/mcp_tools.py`:
`app/mcp_server.py` registers them separately, so they stay directly testable
with no MCP machinery involved.
"""

import asyncio
import logging
from typing import Any

from app.materials.gather import service
from app.materials.ingest.pipeline import ingest

log = logging.getLogger(__name__)

# Ingests kicked off after a tool call returns are fire-and-forget, same as
# the HTTP endpoint's BackgroundTasks -- but there is no BackgroundTasks
# object here, so a bare asyncio task is used instead. Holding a reference in
# this set is what stops asyncio from garbage-collecting a task mid-flight;
# the done-callback below is what stops the set from growing forever.
_background_ingests: set[asyncio.Task] = set()


def _schedule_ingest(file_id: int) -> None:
    task = asyncio.create_task(ingest(file_id))
    _background_ingests.add(task)
    task.add_done_callback(_background_ingests.discard)


async def gather_materials() -> dict[str, Any]:
    """Sync study materials now: scan the local materials inbox and connected
    Google Drive (scoped to one folder and its subfolders if the student has
    set that in Knowledge-Sync, otherwise all of Drive) for anything new or
    changed since the last sync, judge what looks like real coursework, and
    import it automatically -- the same thing the "Sync now" button and the
    scheduled sync do.

    Returns counts, not the imported material itself -- ingestion (extracting
    text, tagging topics, embedding) continues after this returns, so a
    freshly-imported file may not show up in `search_materials` or
    `list_material_topics` for a few seconds yet. Call one of those
    afterward, not this tool again, to check whether it's ready.
    """
    result = await service.run_gather_cycle()

    for file_id in [*result["imported_file_ids"], *result["refreshed_file_ids"]]:
        _schedule_ingest(file_id)

    log.info(
        "gather_materials tool: run %s, %s candidates, %s imported, %s refreshed",
        result["run_id"],
        result["candidates_seen"],
        len(result["imported_file_ids"]),
        len(result["refreshed_file_ids"]),
    )

    return result
