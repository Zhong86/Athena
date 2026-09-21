"""Rows -> response models, and the one rule that matters here: the answer key
stays server-side until the quiz is graded.

`correct_option`, `rubric` and `explanation` are all answer key. A client that
can read them from `GET /quizzes/{id}` before submitting can score 100 without
knowing anything, which makes every understanding score downstream a fiction.
Withholding them in this one function -- rather than in each route -- is what
keeps that true as routes get added.
"""

from typing import Any

from app.quizzes.schemas import Answer, Question, Quiz, QuizSummary


def question(row: dict[str, Any], *, reveal: bool) -> Question:
    return Question(
        id=row["id"],
        order_index=row["order_index"],
        kind=row["kind"],
        prompt=row["prompt"],
        options=row.get("options") or [],
        topic_id=row.get("topic_id"),
        resources=row.get("resources") or [],
        correct_option=row.get("correct_option") if reveal else None,
        rubric=row.get("rubric") if reveal else None,
        explanation=row.get("explanation") if reveal else None,
    )


def answer(row: dict[str, Any]) -> Answer:
    return Answer(
        question_id=row["question_id"],
        kind=row.get("kind"),
        answer=row.get("answer"),
        selected_option=row.get("selected_option"),
        score=row.get("score"),
        correct=row.get("correct"),
        feedback=row.get("feedback"),
        graded_by=row.get("graded_by"),
        graded_at=row.get("graded_at"),
        grading_resources=row.get("grading_resources") or [],
    )


def quiz(
    row: dict[str, Any],
    questions: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> Quiz:
    """`reveal` is derived from the quiz's own status, never from a request
    parameter -- a caller must not be able to ask for the key."""
    reveal = row["status"] == "graded"
    return Quiz(
        id=row["id"],
        session_id=row["session_id"],
        topic_id=row["topic_id"],
        topic_name=row.get("topic_name"),
        title=row["title"],
        status=row["status"],
        score=row.get("score"),
        created_at=row["created_at"],
        submitted_at=row.get("submitted_at"),
        graded_at=row.get("graded_at"),
        grading_error=row.get("grading_error"),
        resources=row.get("resources") or [],
        questions=[question(q, reveal=reveal) for q in questions],
        answers=[answer(a) for a in attempts],
    )


def summary(row: dict[str, Any]) -> QuizSummary:
    return QuizSummary(
        id=row["id"],
        session_id=row["session_id"],
        topic_id=row["topic_id"],
        topic_name=row.get("topic_name"),
        title=row["title"],
        status=row["status"],
        score=row.get("score"),
        question_count=row.get("question_count", 0),
        created_at=row["created_at"],
        graded_at=row.get("graded_at"),
    )
