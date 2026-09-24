from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

QuestionKind = Literal["multiple_choice", "open_ended"]
QuizStatus = Literal["ready", "in_progress", "grading", "graded"]
GradedBy = Literal["key", "hermes"]
UnderstandingSource = Literal["quiz", "session", "manual"]


class Resource(BaseModel):
    """One thing the quiz was written from.

    Two kinds on purpose. A `chunk` is a pointer into our own materials, so it
    carries only an id and the text is rehydrated at grading time -- an edited
    chunk then grades against its current wording. An `external` resource is a
    snapshot, because the backend has no outbound web access and cannot
    re-fetch a page that may have moved or changed since the quiz was built.
    """

    kind: Literal["chunk", "external"]
    chunk_id: int | None = None
    url: str | None = None
    title: str | None = None
    # Excerpt. Required for `external`, ignored for `chunk`.
    text: str | None = None

    @model_validator(mode="after")
    def _require_the_identifying_field(self) -> "Resource":
        if self.kind == "chunk" and self.chunk_id is None:
            raise ValueError("a chunk resource needs chunk_id")
        if self.kind == "external" and not (self.url or "").strip():
            raise ValueError("an external resource needs url")
        if self.kind == "external" and not (self.text or "").strip():
            # Refusing this at the door rather than discovering it during
            # grading: an external resource with no excerpt can never be shown
            # to Hermes, so it would silently contribute nothing to the grade.
            raise ValueError("an external resource needs text -- it cannot be re-fetched")
        return self


# --------------------------------------------------------------------------
# creating a quiz (generation happens elsewhere; this is the hand-off)
# --------------------------------------------------------------------------


class QuestionCreate(BaseModel):
    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=4000)
    options: list[str] = []
    correct_option: int | None = None
    rubric: str | None = None
    explanation: str | None = None
    resources: list[Resource] = []
    topic_id: int | None = None

    @model_validator(mode="after")
    def _kind_matches_its_fields(self) -> "QuestionCreate":
        if self.kind == "multiple_choice":
            if len(self.options) < 2:
                raise ValueError("a multiple choice question needs at least 2 options")
            if self.correct_option is None:
                raise ValueError("a multiple choice question needs correct_option")
            if not 0 <= self.correct_option < len(self.options):
                raise ValueError("correct_option is out of range for options")
        else:
            if self.options:
                raise ValueError("an open-ended question cannot have options")
            if self.correct_option is not None:
                raise ValueError("an open-ended question has no correct_option")
        return self


class QuizCreate(BaseModel):
    topic_id: int
    title: str = Field(min_length=1, max_length=200)
    questions: list[QuestionCreate] = Field(min_length=1)
    resources: list[Resource] = []


# --------------------------------------------------------------------------
# answering
# --------------------------------------------------------------------------


class AnswerSubmit(BaseModel):
    question_id: int
    # Open-ended. Empty string is allowed and grades as a blank answer; the
    # field being absent means the question was skipped.
    answer: str | None = None
    # Multiple choice: index into the question's options.
    selected_option: int | None = None


class AnswersRequest(BaseModel):
    answers: list[AnswerSubmit] = Field(min_length=1)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


class Question(BaseModel):
    """The answer key fields are Optional because they are *withheld* until the
    quiz is graded -- see `view.question`. A client that can read
    `correct_option` before submitting can pass every quiz."""

    id: int
    order_index: int
    kind: QuestionKind
    prompt: str
    options: list[str] = []
    topic_id: int | None = None
    correct_option: int | None = None
    rubric: str | None = None
    explanation: str | None = None
    resources: list[dict[str, Any]] = []


class Answer(BaseModel):
    question_id: int
    kind: str | None = None
    answer: str | None = None
    selected_option: int | None = None
    score: int | None = None
    correct: bool | None = None
    feedback: str | None = None
    graded_by: GradedBy | None = None
    graded_at: str | None = None
    grading_resources: list[dict[str, Any]] = []


class Quiz(BaseModel):
    id: int
    session_id: int
    topic_id: int
    topic_name: str | None = None
    title: str
    status: QuizStatus
    score: int | None = None
    created_at: str
    submitted_at: str | None = None
    graded_at: str | None = None
    grading_error: str | None = None
    resources: list[dict[str, Any]] = []
    questions: list[Question] = []
    answers: list[Answer] = []


class QuizSummary(BaseModel):
    """The list row. No questions -- the Quizzes index renders dozens of these
    and does not need every prompt to do it."""

    id: int
    session_id: int
    topic_id: int
    topic_name: str | None = None
    title: str
    status: QuizStatus
    score: int | None = None
    question_count: int
    created_at: str
    graded_at: str | None = None


class QuizPage(BaseModel):
    items: list[QuizSummary]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# creating a quiz (the agent flow -- generation happens here)
# --------------------------------------------------------------------------


class StartQuizCreation(BaseModel):
    # Optional opening line ("quiz me on entropy"); choose_topic still
    # confirms it against the real topic list rather than trusting it blind.
    topic_hint: str | None = Field(default=None, max_length=200)


class ResumeQuizCreation(BaseModel):
    """The resume payload, passed to the graph as-is.

    Untyped on purpose, same reasoning as `ResumeRoadmap`: `choose_topic`,
    `choose_format` and `present_quiz` each expect a different shape, and only
    the node that called `interrupt()` knows which.
    """

    payload: Any


class QuizCreationRunCard(BaseModel):
    """An unfinished run, for a "pick up where you left off" row."""

    thread_id: str
    status: str
    topic_hint: str | None = None
    created_at: str
    updated_at: str | None = None


class QuizCreationEnvelope(BaseModel):
    """One shape for every graph endpoint, so the frontend has one code path.

    `interrupt` null with a `quiz_id` set means the run committed.
    """

    thread_id: str
    status: str
    interrupt: dict[str, Any] | None = None
    quiz_id: int | None = None


class UnderstandingEvent(BaseModel):
    id: int
    topic_id: int
    source: UnderstandingSource
    quiz_id: int | None = None
    session_id: int | None = None
    previous_understanding: int
    understanding: int
    reason: str
    evidence: dict[str, Any] = {}
    created_at: str
