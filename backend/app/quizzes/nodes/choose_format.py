"""`choose_format` -- step 2: multiple choice, open-ended, or both -- and how many.

Deterministic, like `choose_topic`: nothing here needs Hermes, so a single
`interrupt()` call is enough -- there is no risk of a side effect re-running
ahead of it on resume.
"""

import logging
from typing import Any

from langgraph.types import interrupt

from app.quizzes.state import (
    DEFAULT_QUESTION_COUNT,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    QuizDraftState,
)

log = logging.getLogger(__name__)

_FORMATS = ("multiple_choice", "open_ended", "mixed")
# What a student actually types, folded onto the three canonical values.
_ALIASES = {
    "mc": "multiple_choice",
    "multiple choice": "multiple_choice",
    "multiple-choice": "multiple_choice",
    "open": "open_ended",
    "open-ended": "open_ended",
    "open essay": "open_ended",
    "essay": "open_ended",
    "both": "mixed",
    "all": "mixed",
}


def choose_format(state: QuizDraftState) -> dict[str, Any]:
    answer = interrupt(
        {
            "kind": "choose_format",
            "topic_name": state.get("topic_name"),
            "error": state.get("format_error"),
        }
    )

    fmt, count = _resolve(answer)
    if fmt is None:
        return {
            "format_error": "Say multiple choice, open-ended, or both.",
            "status": "choosing_format",
        }

    return {
        "question_format": fmt,
        "question_count": count,
        "format_error": None,
        "status": "generating",
    }


def _resolve(answer: Any) -> tuple[str | None, int]:
    if isinstance(answer, str):
        answer = {"format": answer}
    if not isinstance(answer, dict):
        return None, DEFAULT_QUESTION_COUNT

    raw = str(answer.get("format") or "").strip().lower()
    fmt = _ALIASES.get(raw, raw)
    if fmt not in _FORMATS:
        return None, DEFAULT_QUESTION_COUNT

    count = answer.get("count")
    count = count if isinstance(count, int) else DEFAULT_QUESTION_COUNT
    return fmt, max(MIN_QUESTIONS, min(MAX_QUESTIONS, count))
