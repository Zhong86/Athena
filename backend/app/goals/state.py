"""`Milestone` and `RoadmapState`, plus the only sanctioned row<->state boundary.

The two TypedDicts come from the Backend Endpoints spec and are implemented
literally. Four fields are added, all additive and all because a screen needs
them; `RoadmapState` itself gains nothing:

    source              widened with "user" -- user-added milestones, NULL in the row
    reason_long         the accordion body
    unlocks_after       prerequisite, by *state* id; the row stores an integer FK
    source_chunk_ids    must survive the approval interrupt, so it cannot be
                        recomputed at commit time -- see personalize.py

`est_effort` stays the spec's free-text string. The row splits it into two
integer columns because the accordion renders a range with a unit, and the
translation lives here rather than in a template.
"""

import json
import sqlite3
from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict

MilestoneStatus = Literal["proposed", "approved", "edited", "rejected"]
MilestoneSource = Literal["materials", "research", "user"]
ProgressStatus = Literal["upcoming", "current", "done"]
RoadmapStatus = Literal[
    "clarifying", "decomposing", "awaiting_approval", "committed", "abandoned"
]

# Bounded per the plan: a list longer than this stops being reviewable in one
# screen, which is the whole premise of show-all approval.
MAX_MILESTONES = 8
# Spec-mandated guard against an unbounded clarify loop.
MAX_CLARIFY_TURNS = 3


class Milestone(TypedDict):
    id: str  # stable id, generated at decomposition time
    title: str
    description: str
    order: int  # user-adjustable sequence position
    status: MilestoneStatus
    reason: str  # why it's here / why it's ordered here
    source: MilestoneSource  # which path produced it
    related_topic_ids: list[int]  # Materials topics, empty if research-sourced
    est_effort: NotRequired[str]
    reason_long: NotRequired[str]
    unlocks_after: NotRequired[str]  # another milestone's *state* id
    source_chunk_ids: NotRequired[list[int]]


class RoadmapState(TypedDict):
    # session/identity -- needed for both tool and endpoint callers
    user_id: str
    session_id: str

    # step 1: clarify
    raw_goal_input: str
    clarifying_questions: NotRequired[list[str]]
    # Quick-reply chips for the parked questions, one list per question. Parked
    # with them because the pass that generates questions and the pass that
    # interrupts to collect answers are two separate node executions.
    suggested_answers: NotRequired[list[list[str]]]
    clarified_goal: NotRequired[str]
    clarification_turns: NotRequired[list[dict]]  # [{question, answer}]

    # step 2: decompose + personalize
    materials_context: NotRequired[dict]
    calendar_context: NotRequired[dict]
    decomposition_source: NotRequired[Literal["materials", "research", "mixed"]]
    draft_milestones: NotRequired[list[Milestone]]

    # step 3: approval
    milestones: NotRequired[list[Milestone]]  # working copy the user edits
    approval_complete: NotRequired[bool]
    # The resume payload, handed from present_for_approval to apply_edits. It has
    # to live in state rather than be passed directly: the two are separate nodes
    # with a checkpoint between them, and the interrupt's return value is only
    # visible to the node that called interrupt().
    pending_action: NotRequired[Any]

    # output
    goal_id: NotRequired[int]
    final_roadmap: NotRequired[list[Milestone]]

    # control
    status: RoadmapStatus

    # Goal-level fields clarify_intent extracts and commit_roadmap persists.
    # Kept in one dict rather than seven top-level keys so the spec's state
    # shape stays recognisable.
    goal_fields: NotRequired[dict]


def new_state(
    raw_goal_input: str, *, user_id: str = "local", session_id: str = ""
) -> RoadmapState:
    return {
        "user_id": user_id,
        "session_id": session_id,
        "raw_goal_input": raw_goal_input,
        "status": "clarifying",
        "clarification_turns": [],
    }


# --------------------------------------------------------------------------
# effort
# --------------------------------------------------------------------------


def format_effort(lo: int | None, hi: int | None) -> str | None:
    """(45, 60) -> "45-60 min". One bound alone is still worth showing."""
    if lo and hi and lo != hi:
        return f"{lo}–{hi} min"
    single = lo or hi
    return f"{single} min" if single else None


def parse_effort(text: str | None) -> tuple[int | None, int | None]:
    """Inverse of format_effort, tolerant of the en-dash, hyphen and "to".

    Needed because a user editing a milestone sends back the rendered string,
    not the two numbers it was built from.
    """
    if not text:
        return None, None
    digits: list[int] = []
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
        elif current:
            digits.append(int(current))
            current = ""
    if current:
        digits.append(int(current))
    if not digits:
        return None, None
    return digits[0], digits[1] if len(digits) > 1 else digits[0]


# --------------------------------------------------------------------------
# row <-> state
# --------------------------------------------------------------------------


def _json_list(raw: Any) -> list:
    """`related_topic_ids` and `source_chunk_ids` are JSON columns. A row
    written by hand (or an older migration default) must not crash a read."""
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def milestone_from_row(row: sqlite3.Row, *, unlocks_after: str | None = None) -> Milestone:
    """`order_index` -> `order`, NULL `source` -> "user", columns -> `est_effort`.

    `unlocks_after` is passed in rather than read from the row: the row holds an
    integer FK and state holds the target's string id, so the caller -- which
    has the whole goal's rows and can therefore resolve the pointer -- supplies
    the translated value.
    """
    milestone: Milestone = {
        "id": row["state_id"] or f"row-{row['id']}",
        "title": row["title"],
        "description": row["description"] or "",
        "order": row["order_index"],
        "status": row["status"],
        "reason": row["reason"] or "",
        # NULL means user-authored with nothing attached. A CHECK constraint
        # passes on NULL, which is what let 001's enum stay unrewritten.
        "source": row["source"] or "user",
        "related_topic_ids": _json_list(row["related_topic_ids"]),
        "source_chunk_ids": _json_list(row["source_chunk_ids"]),
    }
    effort = format_effort(row["est_effort_min"], row["est_effort_max"])
    if effort:
        milestone["est_effort"] = effort
    if row["reason_long"]:
        milestone["reason_long"] = row["reason_long"]
    if unlocks_after:
        milestone["unlocks_after"] = unlocks_after
    return milestone


def milestone_to_params(
    milestone: Milestone,
    goal_id: int,
    *,
    progress_status: ProgressStatus = "upcoming",
) -> dict[str, Any]:
    """`order` -> `order_index`, "user" -> NULL, `est_effort` -> two columns.

    `unlocks_after_id` is deliberately absent: the state id it points at has no
    row id until every milestone is inserted, so commit_roadmap patches it in a
    second pass.
    """
    lo, hi = parse_effort(milestone.get("est_effort"))
    source = milestone.get("source")
    return {
        "goal_id": goal_id,
        "state_id": milestone["id"],
        "title": milestone["title"],
        "description": milestone.get("description") or None,
        "order_index": milestone["order"],
        "status": milestone.get("status", "proposed"),
        "reason": milestone.get("reason") or None,
        "reason_long": milestone.get("reason_long") or None,
        # The row's CHECK only knows materials|research, so "user" -- and
        # anything unrecognised -- goes in as NULL and reads back as "user".
        "source": source if source in ("materials", "research") else None,
        "related_topic_ids": json.dumps(milestone.get("related_topic_ids") or []),
        "source_chunk_ids": json.dumps(milestone.get("source_chunk_ids") or []),
        "est_effort_min": lo,
        "est_effort_max": hi,
        "progress_status": progress_status,
    }


def renumber(milestones: list[Milestone]) -> list[Milestone]:
    """Recompute contiguous 1-based `order` after a reorder/reject/insert.

    Rejected milestones keep their place in the list but are skipped when
    numbering: they are still shown (struck through) until commit drops them,
    and a gap in the visible numbering reads as a bug.
    """
    position = 0
    for milestone in milestones:
        if milestone.get("status") == "rejected":
            continue
        position += 1
        milestone["order"] = position
    return milestones
