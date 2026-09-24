"""Decide which candidates are plausibly real course material.

No user is present to approve picks (this graph is cron-triggered), so this is
the one place standing between "found in Drive/the inbox" and "auto-imported
and ingested." A candidate with no classifiable `upload_type` never reaches
Hermes -- there's nothing to classify, it goes straight to `.skipped/` in
`apply_decisions`.
"""

import logging
from pathlib import Path

import anyio
import anyio.from_thread

from app.config import get_settings
from app.connections import google_drive
from app.db import connection
from app.goals.context import materials_context
from app.goals.llm import LLMUnavailable, ask_json
from app.materials import drive
from app.materials.gather.state import DriveCandidate, GatherState, LocalCandidate
from app.materials.ingest.extract import ExtractError, extract

log = logging.getLogger(__name__)

# Enough to judge substance, not so much every prompt balloons -- same budget
# the tagger uses per chunk, for the same reason (CHUNK_PREVIEW_CHARS).
PREVIEW_CHARS = 400
# A peek, not the import itself: capped well below the real ingest ceiling so
# judging relevance never pays for downloading someone's large file in full.
DRIVE_PREVIEW_BYTE_LIMIT = 20_000

SYSTEM_PROMPT = (
    "You decide which candidate files are genuine course or study material worth "
    "keeping, for a student's personal knowledge base. You reply with JSON only "
    "-- no prose, no code fences."
)


def _run(factory):
    try:
        return anyio.from_thread.run(factory)
    except (google_drive.DriveError, google_drive.DriveNotConnected):
        raise
    except RuntimeError:
        return anyio.run(factory)


def _local_preview(candidate: LocalCandidate) -> str | None:
    """None means "judge from name/size alone" -- never a reason to fail the
    run. Non-text types are skipped outright: OCR/PDF extraction is too
    expensive to pay for on every candidate just to preview it."""
    if candidate["upload_type"] != "text":
        return None
    try:
        data = Path(candidate["path"]).read_bytes()
        text = extract(data, "text")
    except (OSError, ExtractError):
        return None
    return text[:PREVIEW_CHARS]


def _drive_preview(token: str, candidate: DriveCandidate) -> str | None:
    if candidate["upload_type"] != "text":
        return None
    try:
        data = _run(
            lambda: drive.fetch_bytes(
                candidate["drive_file_id"],
                candidate.get("mime_type") or "",
                DRIVE_PREVIEW_BYTE_LIMIT,
            )
        )
        text = extract(data, "text")
    except (google_drive.DriveError, ExtractError):
        return None
    return text[:PREVIEW_CHARS]


def _previews(candidates: list[tuple[str, dict]]) -> list[str | None]:
    """One preview per candidate, same order. Drive auth is resolved once,
    only if some candidate actually needs it -- a run with only local
    candidates never touches Drive at all here."""
    needs_drive = any(
        kind == "drive" and c["upload_type"] == "text" for kind, c in candidates
    )
    token = None
    if needs_drive:
        try:
            token = _run(lambda: google_drive.access_token())
        except (google_drive.DriveError, google_drive.DriveNotConnected):
            token = None  # every drive preview below degrades to name/size only

    previews: list[str | None] = []
    for kind, c in candidates:
        if kind == "local":
            previews.append(_local_preview(c))
        elif token:
            previews.append(_drive_preview(token, c))
        else:
            previews.append(None)
    return previews


def _prompt(candidates: list[tuple[str, dict]], previews: list[str | None], topics: list[dict]) -> str:
    catalogue = (
        "\n".join(f"  - {t['name']}" for t in topics) or "  (nothing uploaded yet)"
    )
    lines = []
    for i, (_, c) in enumerate(candidates):
        header = (
            f"[{i}] {c['name']} ({c['upload_type']}"
            + (f", {c['size']} bytes" if c.get("size") else "")
            + ")"
        )
        preview = previews[i]
        if preview:
            header += f'\n    preview: "{preview.strip()}"'
        lines.append(header)
    listing = "\n".join(lines)

    return f"""The student already has material on:
{catalogue}

Candidate files found in their inbox / connected Drive. A short preview is
included where one could be fetched -- judge those primarily from the
preview, not the name. Where no preview is present (a PDF/image, or a preview
that couldn't be fetched), judge from the name and size as before:
{listing}

Pick the ones that are plausibly real course or study material -- lecture
notes, slides, problem sets, textbook excerpts, readings -- worth adding to
their knowledge base automatically, with no chance to review first. Reject
anything that reads as personal, administrative, or ambiguous (invoices,
"Untitled document", photos, spreadsheets that aren't obviously coursework,
duplicates like "Copy of ...", a preview with no real content). A short file
is not automatically junk if its preview shows real substance -- judge the
content, not the byte count. When in doubt, reject -- a missed file can be
picked up next run, a wrongly-imported one cannot be un-seen by the student.

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

    previews = _previews(candidates)

    with connection() as conn:
        topics = materials_context(conn)["topics"]

    try:
        payload = ask_json(_prompt(candidates, previews, topics), system=SYSTEM_PROMPT)
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
