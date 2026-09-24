"""`present_quiz` -- one last look before the quiz goes live.

Not one of the four steps by name, but the interrupt-before-persist shape
everywhere else in this codebase uses for anything generated (see
`app.goals.nodes.approve`): accepting a generation sight unseen would mean the
first thing a bad batch of questions touches is a student's understanding
score, with no chance to regenerate or bail first.

Unlike the roadmap's show-all approval, there is nothing to edit field by
field here -- just accept, regenerate from scratch, or cancel. A full editing
UI is the roadmap graph's job to have pioneered, not this one's to duplicate.
"""

import logging
from typing import Any

from langgraph.types import interrupt

from app.quizzes.state import QuizDraftState

log = logging.getLogger(__name__)

ACTIONS = ("start", "regenerate", "cancel")


def present_quiz(state: QuizDraftState) -> dict[str, Any]:
    answer = interrupt(
        {
            "kind": "review",
            "quiz_title": state.get("quiz_title"),
            "topic_name": state.get("topic_name"),
            "questions": state.get("draft_questions") or [],
            "error": state.get("review_error"),
        }
    )

    action = _name(answer)
    if action == "start":
        return {"review_error": None, "ready_to_commit": True, "status": "reviewing"}
    if action == "regenerate":
        return {
            "draft_questions": [],
            "review_error": None,
            "ready_to_commit": False,
            "status": "generating",
        }
    if action == "cancel":
        return {"status": "abandoned"}

    return {
        "review_error": "Say start, regenerate, or cancel.",
        "ready_to_commit": False,
        "status": "reviewing",
    }


def _name(answer: Any) -> str | None:
    if isinstance(answer, str):
        return answer if answer in ACTIONS else None
    if isinstance(answer, dict):
        action = answer.get("action")
        return action if action in ACTIONS else None
    return None
