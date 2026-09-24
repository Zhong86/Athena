"""Decide which candidates are plausibly real course material.

No user is present to approve picks (this graph is cron-triggered), so this is
the one place standing between "found in Drive/the inbox" and "auto-imported
and ingested." A candidate with no classifiable `upload_type` never reaches
Hermes -- there's nothing to classify, it goes straight to `.skipped/` in
`apply_decisions`.
"""

import logging

from app.config import get_settings
from app.db import connection
from app.goals.context import materials_context
from app.goals.llm import LLMUnavailable, ask_json
from app.materials.gather.state import DriveCandidate, GatherState, LocalCandidate

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You decide which candidate files are genuine course or study material worth "
    "keeping, for a student's personal knowledge base. You reply with JSON only "
    "-- no prose, no code fences."
)


def _prompt(candidates: list[tuple[str, dict]], topics: list[dict]) -> str:
    catalogue = (
        "\n".join(f"  - {t['name']}" for t in topics) or "  (nothing uploaded yet)"
    )
    listing = "\n".join(
        f"[{i}] {c['name']} ({c['upload_type']}"
        + (f", {c['size']} bytes" if c.get("size") else "")
        + ")"
        for i, (_, c) in enumerate(candidates)
    )
    return f"""The student already has material on:
{catalogue}

Candidate files found in their inbox / connected Drive:
{listing}

Pick the ones that are plausibly real course or study material -- lecture
notes, slides, problem sets, textbook excerpts, readings -- worth adding to
their knowledge base automatically, with no chance to review first. Reject
anything that reads as personal, administrative, or ambiguous from the name
alone (invoices, "Untitled document", photos, spreadsheets that aren't
obviously coursework, duplicates like "Copy of ..."). When in doubt, reject --
a missed file can be picked up next run, a wrongly-imported one cannot be
un-seen by the student.

Reply with JSON of exactly this shape:
{{"selected": [0, 2]}}  // indices into the candidate list above, empty if none qualify"""


def _parse_selected(payload, count: int) -> list[int]:
    """`ask_json` already guarantees valid JSON -- this only guards the shape,
    since a well-formed-but-wrong reply (e.g. `{"foo": ...}`) must not crash."""
    selected = payload.get("selected") if isinstance(payload, dict) else None
    if not isinstance(selected, list):
        return []
    return sorted({i for i in selected if isinstance(i, int) and 0 <= i < count})


def decide_relevance(state: GatherState) -> dict:
    local: list[LocalCandidate] = [
        c for c in state.get("local_candidates") or [] if c["upload_type"]
    ]
    drive_new: list[DriveCandidate] = state.get("drive_new") or []
    candidates: list[tuple[str, dict]] = [("local", c) for c in local] + [
        ("drive", c) for c in drive_new
    ]

    if not candidates:
        return {"selected_local": [], "selected_drive": []}

    with connection() as conn:
        topics = materials_context(conn)["topics"]

    try:
        payload = ask_json(_prompt(candidates, topics), system=SYSTEM_PROMPT)
    except LLMUnavailable as exc:
        # A dead gateway must not auto-import anything -- skip this run's
        # picks rather than guess, the next run tries again.
        log.warning("gather: relevance decision unavailable: %s", exc)
        return {"selected_local": [], "selected_drive": [], "relevance_error": str(exc)}

    indices = _parse_selected(payload, len(candidates))
    indices = indices[: get_settings().materials_gather_max_imports]

    selected_local = [candidates[i][1]["path"] for i in indices if candidates[i][0] == "local"]
    selected_drive = [
        candidates[i][1]["drive_file_id"] for i in indices if candidates[i][0] == "drive"
    ]
    return {"selected_local": selected_local, "selected_drive": selected_drive}
