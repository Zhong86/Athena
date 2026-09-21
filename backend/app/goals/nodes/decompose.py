"""`decompose_goal` -- step 2a: goal -> ordered draft milestones.

No Materials and no Calendar context reaches this node. The spec separates
decomposition from personalization, and mixing them makes both untestable: you
can no longer tell whether a milestone exists because the goal needs it or
because a topic happened to score low.
"""

import logging
from typing import Any

from app.goals.llm import LLMUnavailable, ask_json
from app.goals.state import MAX_MILESTONES, Milestone, RoadmapState

log = logging.getLogger(__name__)

MIN_MILESTONES = 3


def _prompt(goal: str) -> str:
    return f"""Goal: "{goal}"

Break this into {MIN_MILESTONES}-{MAX_MILESTONES} study milestones in the order they
should be worked. Each milestone is one sitting or one focused stretch of work --
not a whole week, not a single exercise.

Reply with JSON only:
{{"milestones": [{{"title": "imperative, under 60 chars",
                  "description": "one or two sentences on what doing it involves"}}]}}

Order them by dependency: what has to be understood before the next thing makes
sense. Do not mention deadlines, scheduling or the student's strengths -- those
are added later from data you cannot see."""


def _state_id(index: int) -> str:
    """Stable ids, assigned once, here.

    Not `uuid4()`: LangGraph can replay a node from a checkpoint, and a
    regenerated id would silently orphan every reorder and reject the student
    had already made against the old one.
    """
    return f"m{index + 1}"


def decompose_goal(state: RoadmapState) -> dict[str, Any]:
    goal = state.get("clarified_goal") or state["raw_goal_input"]

    try:
        reply = ask_json(_prompt(goal))
    except LLMUnavailable as exc:
        # Nothing to fall back on: a roadmap is the product. Surface it as a
        # failed run so the router can 503 and the student can retry once
        # Hermes is up, rather than committing an empty goal.
        log.error("roadmap: decomposition failed (%s)", exc)
        raise

    raw = reply.get("milestones") or []
    drafts: list[Milestone] = []
    for index, item in enumerate(raw[:MAX_MILESTONES]):
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        drafts.append(
            {
                "id": _state_id(len(drafts)),
                "title": title[:120],
                "description": str(item.get("description") or "").strip(),
                "order": len(drafts) + 1,
                "status": "proposed",
                # Filled by personalize_decomposition, which is the only node
                # that knows which path grounded the milestone.
                "reason": "",
                "source": "research",
                "related_topic_ids": [],
            }
        )

    if not drafts:
        raise LLMUnavailable("decomposition produced no usable milestones")

    return {"draft_milestones": drafts, "status": "decomposing"}
