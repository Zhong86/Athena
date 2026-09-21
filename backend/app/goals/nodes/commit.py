"""`commit_roadmap` -- persist the approved roadmap and hand back a `goal_id`.

One transaction for the goal and every milestone. A goal holding half a roadmap
is worse than no goal: the student sees a plan that was never approved and has no
way to tell which half is missing.
"""

import logging
from typing import Any

from app.db import connection
from app.goals import repository as repo
from app.goals.state import RoadmapState

log = logging.getLogger(__name__)


def _title(state: RoadmapState) -> str:
    fields = state.get("goal_fields") or {}
    for candidate in (fields.get("title"), state.get("clarified_goal"), state["raw_goal_input"]):
        if candidate and str(candidate).strip():
            return str(candidate).strip()[:200]
    return "Untitled goal"


def commit_roadmap(state: RoadmapState) -> dict[str, Any]:
    approved = [m for m in (state.get("milestones") or []) if m.get("status") != "rejected"]
    fields = state.get("goal_fields") or {}

    category = fields.get("category")
    with connection() as conn:
        goal = repo.create_goal(
            conn,
            title=_title(state),
            short_name=(fields.get("short_name") or None),
            course_code=(fields.get("course_code") or None),
            category=category if category in ("academic", "career") else "academic",
            due_at=(fields.get("due_at") or None),
            description=state.get("clarified_goal") or None,
            derivation=(fields.get("derivation") or None),
            # Goal-level "why this order" copy. Built from the milestones' own
            # reasons rather than a fresh LLM call: a second narration of the
            # same ordering is a second chance to contradict it.
            order_rationale=_order_rationale(state),
            status="committed",
        )
        repo.insert_roadmap(conn, goal["id"], approved)

    log.info("roadmap: committed goal %s with %d milestones", goal["id"], len(approved))
    return {
        "goal_id": goal["id"],
        "final_roadmap": approved,
        "status": "committed",
        "approval_complete": True,
    }


def _order_rationale(state: RoadmapState) -> str | None:
    """One sentence naming what drove the ordering, or nothing.

    Returns None when there is no ranking signal to cite -- an empty Materials
    table and no calendar means the order came from the decomposition alone, and
    claiming otherwise on the detail page would be the exact "template" the copy
    promises this is not.
    """
    milestones = state.get("milestones") or []
    ranked = [m for m in milestones if m.get("source") == "materials" and m.get("reason")]
    if not ranked:
        return None
    deadlines = (state.get("calendar_context") or {}).get("deadlines") or []
    lead = ranked[0]
    # The milestone's own reason is a finished sentence, so it is lower-cased into
    # this one and its full stop is not doubled.
    because = f"{lead['reason'][0].lower()}{lead['reason'][1:]}".rstrip(".")
    if deadlines:
        return (
            f"Ordered from your check-ins and {len(deadlines)} upcoming deadline"
            f"{'s' if len(deadlines) > 1 else ''} — "
            f"“{lead['title']}” came first because {because}."
        )
    return f"Ordered from your check-ins — “{lead['title']}” came first because {because}."
