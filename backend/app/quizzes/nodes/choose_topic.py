"""`choose_topic` -- step 1: get materials, clarify which topic the quiz covers.

No LLM call: the list of topics with material *is* the whole prompt, and there
is nothing to decide beyond matching whatever the student picked against it.
Reading that list is cheap and idempotent, so -- unlike `clarify_intent` --
this node does not need the two-pass "interrupt must be the first statement"
structure; the only thing before the `interrupt()` call is a plain SELECT, and
re-running it on every resume is harmless (even desirable: it reflects
material uploaded since the run started).
"""

import logging
from typing import Any

from langgraph.types import interrupt

from app.db import connection
from app.quizzes.context import topics_with_material
from app.quizzes.state import NoMaterials, QuizDraftState

log = logging.getLogger(__name__)


def choose_topic(state: QuizDraftState) -> dict[str, Any]:
    with connection() as conn:
        topics = topics_with_material(conn)

    if not topics:
        # Nothing to fall back on: a quiz has to be grounded in the student's
        # own material, so there is no research branch the way the roadmap
        # graph has one. Surface it as a failed run rather than committing a
        # quiz with no evidence behind it.
        raise NoMaterials(
            "No uploaded material yet -- upload something in Materials before creating a quiz."
        )

    answer = interrupt(
        {
            "kind": "choose_topic",
            "topics": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "description": t.get("description"),
                    "chunk_count": t["chunk_count"],
                }
                for t in topics
            ],
            "topic_hint": state.get("topic_hint"),
            "error": state.get("topic_error"),
        }
    )

    topic = _resolve(answer, topics)
    if topic is None:
        return {
            "topic_error": "Please choose one of the listed topics.",
            "status": "choosing_topic",
        }

    return {
        "topic_id": topic["id"],
        "topic_name": topic["name"],
        "topic_error": None,
        "status": "choosing_format",
    }


def _resolve(answer: Any, topics: list[dict]) -> dict | None:
    if isinstance(answer, dict):
        topic_id = answer.get("topic_id")
        if isinstance(topic_id, int):
            return next((t for t in topics if t["id"] == topic_id), None)
        name = answer.get("topic_name")
        return _match_by_name(str(name), topics) if name else None
    if isinstance(answer, str) and answer.strip():
        return _match_by_name(answer, topics)
    return None


def _match_by_name(name: str, topics: list[dict]) -> dict | None:
    """Exact match first, then substring either direction -- the same
    leniency `find_topic_by_name` gives a model-typed name."""
    needle = name.strip().lower()
    if not needle:
        return None
    for t in topics:
        if t["name"].lower() == needle:
            return t
    for t in topics:
        if needle in t["name"].lower() or t["name"].lower() in needle:
            return t
    return None
