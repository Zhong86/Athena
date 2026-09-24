"""`GatherState` -- the state threaded through the gather graph.

Deliberately flat and `NotRequired`-heavy like `app.goals.state.RoadmapState`:
each node only adds what its stage produces, and a node run in isolation (as
the tests do) should not have to fabricate fields it doesn't touch.
"""

from typing import Any

from typing_extensions import NotRequired, TypedDict


class LocalCandidate(TypedDict):
    path: str  # absolute path, still inside materials_inbox_path
    name: str
    # None when the extension/content-type can't be classified -- decide_relevance
    # auto-rejects these without asking Hermes, apply_decisions moves them to
    # .skipped/ like any other rejection.
    upload_type: str | None
    size: int


class DriveCandidate(TypedDict):
    # Same shape `app.materials.drive.as_drive_file()` already produces --
    # reused as-is rather than re-mapped into a second shape.
    drive_file_id: str
    name: str
    mime_type: str
    upload_type: str
    modified_at: str | None
    size: int | None
    web_view_link: str | None
    exported: bool
    source_file_id: int | None


class GatherState(TypedDict):
    run_id: int
    # The modifiedTime boundary this run's Drive query is scoped to. Withheld
    # (left as this run's start) if the scan hits its cap -- see scan_drive.py.
    drive_cursor: str | None

    local_candidates: NotRequired[list[LocalCandidate]]
    # Drive files never seen before -- these go through decide_relevance.
    drive_new: NotRequired[list[DriveCandidate]]
    # Drive files already imported, but with a newer modifiedTime -- these
    # bypass relevance scoring entirely, see scan_drive.py.
    drive_refresh: NotRequired[list[DriveCandidate]]
    # True if the Drive side of this run cannot be trusted as a complete scan
    # -- not connected, a call failed, or the page cap was hit. The router
    # must not advance the persisted cursor when this is set, or files that
    # were never actually seen would silently stop being considered.
    drive_incomplete: NotRequired[bool]

    selected_local: NotRequired[list[str]]  # LocalCandidate.path values
    selected_drive: NotRequired[list[str]]  # DriveCandidate.drive_file_id values
    relevance_error: NotRequired[str]  # set when decide_relevance degrades

    imported_file_ids: NotRequired[list[int]]
    refreshed_file_ids: NotRequired[list[int]]
    skipped_local: NotRequired[list[str]]  # names moved into .skipped/


def new_state(run_id: int, drive_cursor: str | None) -> GatherState:
    return {"run_id": run_id, "drive_cursor": drive_cursor}


def result_summary(state: dict[str, Any]) -> dict[str, Any]:
    """The counts `apply_decisions` hands back to the router for `finish_run`
    and the HTTP response -- one place so the two cannot disagree."""
    return {
        "imported_file_ids": state.get("imported_file_ids") or [],
        "refreshed_file_ids": state.get("refreshed_file_ids") or [],
        "skipped_local": state.get("skipped_local") or [],
        "candidates_seen": (
            len(state.get("local_candidates") or [])
            + len(state.get("drive_new") or [])
            + len(state.get("drive_refresh") or [])
        ),
    }
