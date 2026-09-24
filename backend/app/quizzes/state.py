"""`QuestionDraft` and `QuizDraftState` -- the quiz-creation graph's state.

Mirrors `app.goals.state`'s shape: plain TypedDicts for the graph's internal
state, kept separate from `app.quizzes.schemas` -- that module is the HTTP
boundary (Pydantic, request/response validation); this is what the LangGraph
checkpointer actually stores between interrupts.
"""

from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict

QuestionKind = Literal["multiple_choice", "open_ended"]
QuestionFormat = Literal["multiple_choice", "open_ended", "mixed"]
QuizDraftStatus = Literal[
    "choosing_topic",
    "choosing_format",
    "generating",
    "reviewing",
    "committed",
    "abandoned",
]

# Bounded for the same reason MAX_MILESTONES is: a quiz longer than this stops
# being one sitting, and a generation prompt asked for more starts padding
# with weaker questions.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 10
DEFAULT_QUESTION_COUNT = 5


class QuestionDraft(TypedDict):
    kind: QuestionKind
    prompt: str
    options: list[str]
    correct_option: int | None
    rubric: str | None
    explanation: str | None
    resources: list[dict[str, Any]]


class QuizDraftState(TypedDict):
    # step 1: topic
    topic_hint: NotRequired[str]
    topic_id: NotRequired[int]
    topic_name: NotRequired[str]
    topic_error: NotRequired[str]

    # step 2: format
    question_format: NotRequired[QuestionFormat]
    question_count: NotRequired[int]
    format_error: NotRequired[str]

    # step 3: generation
    quiz_title: NotRequired[str]
    draft_questions: NotRequired[list[QuestionDraft]]
    resources: NotRequired[list[dict[str, Any]]]
    review_error: NotRequired[str]
    # Set by present_quiz's "start" action; read by the conditional edge that
    # routes to commit_quiz. Kept separate from `status` so `status` never
    # needs a value outside quiz_creation_runs' CHECK constraint.
    ready_to_commit: NotRequired[bool]

    # output
    quiz_id: NotRequired[int]

    # control
    status: QuizDraftStatus


def new_state(*, topic_hint: str | None = None) -> QuizDraftState:
    state: QuizDraftState = {"status": "choosing_topic"}
    if topic_hint and topic_hint.strip():
        state["topic_hint"] = topic_hint.strip()
    return state


class NoMaterials(RuntimeError):
    """Raised when there is nothing uploaded/ingested to build a quiz from."""
