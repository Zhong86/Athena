"""`present_for_approval` and `apply_edits` -- step 3, show-all only.

There is no step-through mode (decision §9.1): the whole draft is interrupted on
at once, every time, and there is no per-milestone approval state to track. The
resume payload carries one action; `apply_edits` applies it deterministically and
routes straight back here, so the student always re-confirms against the list they
just changed rather than having silence read as consent.
"""

import logging
from typing import Any

from langgraph.types import interrupt

from app.db import connection
from app.goals.state import MAX_MILESTONES, Milestone, RoadmapState, renumber
from app.materials import repository as materials_repo

log = logging.getLogger(__name__)

ACTIONS = ("approve_all", "reorder", "edit", "reject", "add_milestone")
EDITABLE = ("title", "description", "reason", "reason_long", "est_effort")


def present_for_approval(state: RoadmapState) -> dict[str, Any]:
    # First entry copies the drafts across; later entries keep the working copy
    # the student has been editing.
    milestones = state.get("milestones") or [dict(m) for m in (state.get("draft_milestones") or [])]

    action = interrupt(
        {
            "kind": "approval",
            "milestones": milestones,
            "decomposition_source": state.get("decomposition_source"),
            # The clarify transcript rides along so the collapsed step-1/2
            # history can be redrawn after a reload without a second request.
            "clarification_turns": state.get("clarification_turns") or [],
            "clarified_goal": state.get("clarified_goal"),
        }
    )

    if _name(action) == "approve_all":
        # Handled here, not in apply_edits: approval is not an edit, and
        # apply_edits unconditionally routes back to this node -- so an
        # approve_all sent down that path could never reach commit_roadmap. The
        # conditional edge reads `approval_complete`, so it has to be set by the
        # node the edge hangs off.
        return {
            "milestones": _approve_all(milestones),
            "approval_complete": True,
            "pending_action": None,
            "status": "awaiting_approval",
        }

    return {
        "milestones": milestones,
        "pending_action": action,
        "status": "awaiting_approval",
    }


def _name(action: Any) -> str | None:
    if isinstance(action, str):
        return action
    if isinstance(action, dict):
        return action.get("action") or action.get("kind")
    return None


def _approve_all(milestones: list[Milestone]) -> list[Milestone]:
    """Everything not rejected becomes approved; rejected milestones are dropped.

    An edited milestone keeps `edited` rather than being flattened to `approved`:
    the spec distinguishes them, and it is what lets the UI show what the student
    changed after the fact.
    """
    kept: list[Milestone] = []
    for milestone in milestones:
        if milestone.get("status") == "rejected":
            continue
        if milestone.get("status") != "edited":
            milestone["status"] = "approved"
        kept.append(milestone)
    return renumber(kept)


def apply_edits(state: RoadmapState) -> dict[str, Any]:
    """Deterministic. No LLM call anywhere in here."""
    milestones: list[Milestone] = [dict(m) for m in (state.get("milestones") or [])]  # type: ignore[misc]
    action = state.get("pending_action") or {}
    if isinstance(action, str):
        action = {"action": action}
    name = _name(action)

    if name == "reorder":
        milestones = _reorder(milestones, [str(i) for i in action.get("ids_in_order") or []])
    elif name == "edit":
        _edit(milestones, str(action.get("milestone_id")), action.get("fields") or {})
    elif name == "reject":
        _reject(milestones, str(action.get("milestone_id")))
    elif name == "add_milestone":
        _add(milestones, action.get("fields") or action)
    else:
        log.warning("roadmap: ignoring unknown approval action %r", name)

    return {
        "milestones": renumber(milestones),
        "approval_complete": False,
        "pending_action": None,
    }


def _reorder(milestones: list[Milestone], ids_in_order: list[str]) -> list[Milestone]:
    """Reorder to the given sequence, keeping anything the payload omitted.

    A partial list is tolerated here -- unlike the post-commit HTTP reorder,
    which rejects it -- because this list is in memory and cannot end up with
    duplicate order values: the omitted milestones are simply appended in their
    existing relative order.
    """
    by_id = {m["id"]: m for m in milestones}
    ordered = [by_id.pop(i) for i in ids_in_order if i in by_id]
    return ordered + [m for m in milestones if m["id"] in by_id]


def _edit(milestones: list[Milestone], milestone_id: str, fields: dict[str, Any]) -> None:
    for milestone in milestones:
        if milestone["id"] != milestone_id:
            continue
        for key in EDITABLE:
            if key in fields and fields[key] is not None:
                milestone[key] = str(fields[key])  # type: ignore[literal-required]
        # "edited" is a status the spec defines; keeping it distinct from
        # "approved" is what lets the UI show what the student changed.
        if milestone.get("status") != "rejected":
            milestone["status"] = "edited"
        return
    log.warning("roadmap: edit for unknown milestone %r", milestone_id)


def _reject(milestones: list[Milestone], milestone_id: str) -> None:
    for milestone in milestones:
        if milestone["id"] == milestone_id:
            milestone["status"] = "rejected"
            # Anything gated on a rejected milestone loses its prerequisite
            # rather than pointing at something the student removed.
            for other in milestones:
                if other.get("unlocks_after") == milestone_id:
                    other.pop("unlocks_after", None)
            return
    log.warning("roadmap: reject for unknown milestone %r", milestone_id)


def _add(milestones: list[Milestone], fields: dict[str, Any]) -> None:
    """Insert a student-authored milestone.

    `source` is "user" when nothing is attached (decision §9.2) and "materials"
    when topics are. `reason` is fixed copy, not an LLM call: the student just
    decided why this belongs, and narrating their intent back at them is worse
    than saying nothing.
    """
    active = [m for m in milestones if m.get("status") != "rejected"]
    if len(active) >= MAX_MILESTONES:
        log.warning("roadmap: refusing to add beyond %d milestones", MAX_MILESTONES)
        return

    title = str(fields.get("title") or "").strip()
    if not title:
        log.warning("roadmap: add_milestone with no title")
        return

    topic_ids = [
        int(t) for t in (fields.get("related_topic_ids") or []) if str(t).lstrip("-").isdigit()
    ]
    if name := str(fields.get("new_topic_name") or "").strip():
        topic_ids.append(_ensure_topic(name))
    milestone: Milestone = {
        "id": _next_id(milestones),
        "title": title[:120],
        "description": str(fields.get("description") or "").strip(),
        "order": 0,  # renumber() fixes this
        "status": "edited",
        "reason": "Added by you.",
        "source": "materials" if topic_ids else "user",
        "related_topic_ids": topic_ids,
        "source_chunk_ids": [],
    }
    if fields.get("est_effort"):
        milestone["est_effort"] = str(fields["est_effort"])

    position = fields.get("position")
    if isinstance(position, int) and 0 <= position <= len(milestones):
        milestones.insert(position, milestone)
    else:
        milestones.append(milestone)


def _ensure_topic(name: str) -> int:
    """Find or create the topic a student named while adding a milestone.

    Created with `auto_created = 0`: that flag exists to separate topics Hermes
    invented during ingestion from topics the student named, and Materials shows
    the difference. Reuses an existing topic on a name match rather than creating
    a near-duplicate the tagger would then have to compete with.
    """
    with connection() as conn:
        existing = materials_repo.find_topic_by_name(conn, name)
        if existing:
            return int(existing["id"])
        created = materials_repo.create_topic(conn, name=name, auto_created=False)
        return int(created["id"])


def _next_id(milestones: list[Milestone]) -> str:
    """Never reuses an id, including ones freed by a reject: a student could
    reject `m3` and add a milestone in the same session, and two different things
    sharing an id would make the next reorder ambiguous."""
    used = {m["id"] for m in milestones}
    index = len(milestones) + 1
    while f"m{index}" in used:
        index += 1
    return f"m{index}"
