"""Whole-app reset -- the Settings page's "Reset everything" button.

Single-user, no auth, so there is no per-account boundary to preserve: reset
means every table goes back to empty and every derived store (the vector
index, uploaded bytes) goes back to nothing, same posture as
`materials/gather/router.py`'s TEST_MODE reset but scoped to the whole app
rather than just materials. `schema_migrations` is left alone -- it tracks
this file's own schema, not user data.
"""

import logging
import shutil

import anyio.to_thread
from fastapi import APIRouter

from app.config import get_settings
from app.connections import crypto, google_oauth, hermes_files
from app.connections import repository as connections_repo
from app.db import connection
from app.materials import vectors as vector_store
from app.system.schemas import ResetResult

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/reset", response_model=ResetResult)
async def reset_everything() -> ResetResult:
    """Wipes every table, the vector store and uploaded bytes back to empty.

    Google is disconnected the same way the Settings card's own Disconnect
    button does it -- revoke upstream, remove the Hermes files -- so a live
    grant doesn't outlive the data it was used to build. That best-effort
    cleanup can fail without blocking the rest of the reset; a failure comes
    back as a warning instead of a 5xx, same as `disconnect_google`.
    """
    with connection() as conn:
        try:
            stored = connections_repo.get_credentials(conn, connections_repo.GOOGLE_SLUG)
        except crypto.SecretUnreadable:
            stored = None

    warnings: list[str] = []
    refresh_token = ((stored or {}).get("token") or {}).get("refresh_token")
    if refresh_token:
        failure = await google_oauth.revoke(refresh_token)
        if failure:
            warnings.append(failure)
    for name in (hermes_files.GOOGLE_TOKEN_FILE, hermes_files.GOOGLE_CLIENT_FILE):
        try:
            await hermes_files.delete_file(name)
        except hermes_files.HermesFileError as exc:
            warnings.append(f"{name} may still be on the Hermes host: {exc}")

    with connection() as conn:
        for row in connections_repo.list_all(conn):
            connections_repo.clear(conn, row["slug"])

        # chunks cascade off source_files; quizzes, quiz_questions,
        # quiz_attempts and understanding_events all cascade off topics (every
        # one of them references topic_id ON DELETE CASCADE) -- so deleting
        # source_files then topics clears the whole materials-and-quiz chain
        # in one go, same order `materials/gather/router.py`'s reset uses.
        conn.execute("DELETE FROM source_files")
        conn.execute("DELETE FROM topics")
        # milestones cascade off goals; roadmap_runs.goal_id CASCADEs too, but
        # only for rows tied to a goal -- an in-flight, uncommitted run has
        # goal_id NULL and needs its own delete, same for quiz_creation_runs.
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM roadmap_runs")
        conn.execute("DELETE FROM quiz_creation_runs")
        # Chat/quiz/cron sessions -- quizzes and quiz_attempts referencing them
        # are already gone via the topics cascade above.
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM materials_gather_runs")
        # The gather interval and Drive folder scope: reads fall back to their
        # defaults on a missing row, so clearing is safe.
        conn.execute("DELETE FROM settings")

    await anyio.to_thread.run_sync(vector_store.drop)

    uploads_root = get_settings().uploads_path
    if uploads_root.exists():
        shutil.rmtree(uploads_root)
    uploads_root.mkdir(parents=True, exist_ok=True)

    log.warning("system reset: entire app state wiped")
    return ResetResult(warning="; ".join(warnings) if warnings else None)
