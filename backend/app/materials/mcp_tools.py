"""Hermes-facing tool functions over Materials search.

Plain callables, not decorated: `app/mcp_server.py` registers them with
`MCPServer.add_tool()` as a separate step, so these stay testable exactly like
any other function in this package -- no MCP machinery required to call them
directly, matching `search_materials`'s own "thin wrapper" framing.
"""

from typing import Any

import anyio.to_thread

from app.db import connection
from app.materials import repository as repo
from app.materials import search as search_module


async def search_materials(
    query: str, topic: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """Search the student's uploaded/ingested notes.

    `topic` is a name, not an id -- call `list_material_topics` first to see
    real topic names. An unrecognised topic still searches everything rather
    than returning nothing; `topic_resolved`/`topic_matched` in the response
    say what happened so the caller can say so instead of implying a filter
    was applied.
    """
    limit = max(1, min(limit, 50))  # mirrors SearchRequest's ge=1/le=50
    return await anyio.to_thread.run_sync(
        lambda: search_module.search_materials(topic, query, limit=limit)
    )


async def list_material_topics() -> list[dict[str, Any]]:
    """List existing Materials topics (id, name, description) so a caller can
    pick a real topic name before calling `search_materials`."""

    def _list() -> list[dict[str, Any]]:
        with connection() as conn:
            return repo.list_topics_for_prompt(conn)

    return await anyio.to_thread.run_sync(_list)
