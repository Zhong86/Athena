"""HTTP for quizzes.

Generation is not here. A quiz arrives fully formed on `POST /quizzes` — with
the resources it was written from — and this module covers what happens
afterwards: answering, grading, and the score that grading moves.

The grading split mirrors materials ingest. Multiple choice is settled inline
because it is arithmetic against a key; the open-ended pass is one Hermes call
per answer and runs as a background task, so submitting a ten-question quiz
returns immediately instead of holding the request open for ten round-trips.
`status` is how a client tells the difference, and `GET /quizzes/{id}` is the
poll.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response

from app.db import connection
from app.materials import repository as materials_repo
from app.quizzes import grading
from app.quizzes import repository as repo
from app.quizzes import scoring, view
from app.quizzes.schemas import (
    AnswersRequest,
    Quiz,
    QuizCreate,
    QuizPage,
    QuizStatus,
    UnderstandingEvent,
)
from app.sessions import repository as sessions_repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


def _load(conn, quiz_id: int) -> dict:
    quiz = repo.get_quiz(conn, quiz_id)
    if quiz is None:
        raise HTTPException(404, f"quiz {quiz_id} not found")
    return quiz


def _hydrate(conn, quiz: dict) -> Quiz:
    return view.quiz(
        quiz,
        repo.questions_for_quiz(conn, quiz["id"]),
        repo.attempts_for_quiz(conn, quiz["id"]),
    )


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


@router.get("", response_model=QuizPage)
def list_quizzes(
    topic_id: int | None = Query(None),
    status: QuizStatus | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> QuizPage:
    with connection() as conn:
        return QuizPage(
            items=[
                view.summary(q)
                for q in repo.list_quizzes(
                    conn, topic_id=topic_id, status=status, limit=limit, offset=offset
                )
            ],
            total=repo.count_quizzes(conn, topic_id=topic_id, status=status),
            limit=limit,
            offset=offset,
        )


# Declared before `/{quiz_id}` so the literal path is matched first.
@router.get("/evidence/{topic_id}", response_model=list[UnderstandingEvent])
def understanding_evidence(
    topic_id: int, limit: int = Query(20, ge=1, le=100)
) -> list[UnderstandingEvent]:
    """"Why is this topic scored 72?" — the answer, newest first.

    Lives under /quizzes because quizzes are what write these rows today, and
    the Materials topic page reads them.
    """
    with connection() as conn:
        if materials_repo.get_topic(conn, topic_id) is None:
            raise HTTPException(404, f"topic {topic_id} not found")
        return [UnderstandingEvent(**e) for e in repo.understanding_events(conn, topic_id, limit=limit)]


@router.get("/{quiz_id}", response_model=Quiz)
def get_quiz(quiz_id: int) -> Quiz:
    with connection() as conn:
        return _hydrate(conn, _load(conn, quiz_id))


# --------------------------------------------------------------------------
# creating
# --------------------------------------------------------------------------


@router.post("", response_model=Quiz, status_code=201)
def create_quiz(body: QuizCreate) -> Quiz:
    """Accepts an already-generated quiz.

    The matching `sessions` row is created here, in the same transaction, so a
    quiz can never exist outside the Sessions log — the log is meant to be the
    complete record of what the agent did, and a quiz that skipped it would be
    invisible there forever.
    """
    with connection() as conn:
        topic = materials_repo.get_topic(conn, body.topic_id)
        if topic is None:
            raise HTTPException(404, f"topic {body.topic_id} not found")

        for q in body.questions:
            if q.topic_id is not None and materials_repo.get_topic(conn, q.topic_id) is None:
                raise HTTPException(404, f"topic {q.topic_id} not found")

        session = sessions_repo.create(
            conn,
            type="quiz",
            payload={"quiz_title": body.title, "topic_id": body.topic_id},
            summary=f"Quiz ready: {body.title}",
        )
        quiz = repo.create_quiz(
            conn,
            session_id=session["id"],
            topic_id=body.topic_id,
            title=body.title,
            resources=[r.model_dump() for r in body.resources],
        )
        repo.add_questions(
            conn, quiz["id"], [q.model_dump() for q in body.questions]
        )
        return _hydrate(conn, _load(conn, quiz["id"]))


@router.delete("/{quiz_id}", status_code=204)
def delete_quiz(quiz_id: int) -> Response:
    """The understanding events this quiz produced are deliberately kept — with
    a NULL quiz_id — so deleting a quiz cannot rewrite the history of a score
    it already caused."""
    with connection() as conn:
        session_id = repo.delete_quiz(conn, quiz_id)
        if session_id is None:
            raise HTTPException(404, f"quiz {quiz_id} not found")
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    return Response(status_code=204)


# --------------------------------------------------------------------------
# answering
# --------------------------------------------------------------------------


@router.post("/{quiz_id}/answers", response_model=Quiz)
def save_answers(quiz_id: int, body: AnswersRequest) -> Quiz:
    """Save answers without grading. Partial and repeatable.

    Only the questions named in the payload are touched, so a client can save
    as the user moves through the quiz rather than having to resend everything.
    """
    with connection() as conn:
        quiz = _load(conn, quiz_id)
        if quiz["status"] in ("grading", "graded"):
            raise HTTPException(409, f"quiz {quiz_id} is already {quiz['status']}")

        by_id = {q["id"]: q for q in repo.questions_for_quiz(conn, quiz_id)}
        for submitted in body.answers:
            question = by_id.get(submitted.question_id)
            if question is None:
                raise HTTPException(
                    404, f"question {submitted.question_id} is not in quiz {quiz_id}"
                )
            repo.save_answer(
                conn,
                quiz_id=quiz_id,
                session_id=quiz["session_id"],
                question=question,
                topic_id=question["topic_id"] or quiz["topic_id"],
                answer=submitted.answer,
                selected_option=submitted.selected_option,
            )

        if quiz["status"] == "ready":
            repo.set_status(conn, quiz_id, "in_progress")
        return _hydrate(conn, _load(conn, quiz_id))


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------


@router.post("/{quiz_id}/submit", response_model=Quiz)
def submit_quiz(quiz_id: int, tasks: BackgroundTasks, response: Response) -> Quiz:
    """Grade the whole quiz.

    Returns 200 with the finished quiz when nothing needed Hermes, and 202 with
    `status: "grading"` when the open-ended pass is still running.
    """
    with connection() as conn:
        quiz = _load(conn, quiz_id)
        if quiz["status"] == "grading":
            raise HTTPException(409, f"quiz {quiz_id} is already being graded")

        questions = repo.questions_for_quiz(conn, quiz_id)
        answered = {a["question_id"] for a in repo.attempts_for_quiz(conn, quiz_id)}
        if not answered:
            raise HTTPException(409, f"quiz {quiz_id} has no answers to grade")

        # Unanswered questions get a blank attempt rather than being skipped:
        # the score is out of the whole quiz, so a skipped question has to cost
        # the same as a wrong one. Leaving them out would mean answering one
        # question correctly scores 100.
        for question in questions:
            if question["id"] not in answered:
                repo.save_answer(
                    conn,
                    quiz_id=quiz_id,
                    session_id=quiz["session_id"],
                    question=question,
                    topic_id=question["topic_id"] or quiz["topic_id"],
                    answer=None,
                    selected_option=None,
                )

        repo.set_status(conn, quiz_id, "grading", submitted=True)

        # Multiple choice is arithmetic against the key -- no reason to make
        # the user wait on a background pass for it.
        by_question = {q["id"]: q for q in questions}
        open_ended = False
        for attempt in repo.attempts_for_quiz(conn, quiz_id):
            question = by_question[attempt["question_id"]]
            if question["kind"] == "open_ended":
                open_ended = True
                continue
            grade = grading.grade_multiple_choice(question, attempt["selected_option"])
            repo.record_grade(
                conn,
                attempt["id"],
                score=grade.score,
                correct=grade.correct,
                feedback=grade.feedback,
                graded_by=grade.graded_by,
                resources=grade.resources,
            )

        if not open_ended:
            _finalize(conn, quiz_id)
            return _hydrate(conn, _load(conn, quiz_id))

    tasks.add_task(grade_open_ended_pass, quiz_id)
    response.status_code = 202
    with connection() as conn:
        return _hydrate(conn, _load(conn, quiz_id))


async def grade_open_ended_pass(quiz_id: int) -> None:
    """One Hermes call per open-ended answer, then finalize.

    The connection is not held across the calls: it is opened to read, closed
    for the round-trips, and reopened to write. Grading ten answers can take a
    minute, and a write transaction left open that long on a WAL database
    blocks every other request for no benefit.
    """
    with connection() as conn:
        quiz = repo.get_quiz(conn, quiz_id)
        if quiz is None:
            return  # deleted mid-grade; nothing to write back to
        questions = {q["id"]: q for q in repo.questions_for_quiz(conn, quiz_id)}
        attempts = [
            a
            for a in repo.attempts_for_quiz(conn, quiz_id)
            if questions[a["question_id"]]["kind"] == "open_ended"
        ]
        # Chunk resources are pointers; resolve them to current text now, in the
        # one round-trip the hydration helper was written for.
        chunk_ids = _referenced_chunk_ids(quiz, [questions[a["question_id"]] for a in attempts])
        chunk_texts = materials_repo.get_chunks(conn, chunk_ids)

    graded: list[tuple[int, grading.Grade]] = []
    failure: str | None = None
    for attempt in attempts:
        question = questions[attempt["question_id"]]
        resources = grading.resolve_resources(question, quiz["resources"], chunk_texts)
        try:
            graded.append(
                (attempt["id"], await grading.grade_open_ended(question, attempt["answer"], resources))
            )
        except grading.GradingUnavailable as exc:
            # Keep the grades already earned and stop: the remaining answers
            # would fail the same way, and `_finalize` reports the shortfall
            # rather than scoring the quiz on half its questions.
            log.warning("quiz %s: open-ended grading failed: %s", quiz_id, exc)
            failure = str(exc)
            break

    with connection() as conn:
        for attempt_id, grade in graded:
            repo.record_grade(
                conn,
                attempt_id,
                score=grade.score,
                correct=grade.correct,
                feedback=grade.feedback,
                graded_by=grade.graded_by,
                resources=grade.resources,
            )
        _finalize(conn, quiz_id, failure=failure)


def _referenced_chunk_ids(quiz: dict, questions: list[dict]) -> list[int]:
    ids: list[int] = []
    for resources in [quiz["resources"], *(q["resources"] for q in questions)]:
        for resource in resources or []:
            if resource.get("kind") == "chunk" and isinstance(resource.get("chunk_id"), int):
                ids.append(resource["chunk_id"])
    return sorted(set(ids))


def _finalize(conn, quiz_id: int, *, failure: str | None = None) -> None:
    """Score the quiz, and move the topics it measured — but only if every
    answer was graded.

    A partially graded quiz is left with no score at all rather than the average
    of what succeeded. The multiple-choice half is systematically the easier
    half, so scoring on it alone would inflate the topic every time Hermes was
    down, and the user would have no way to see that had happened.
    """
    quiz = repo.get_quiz(conn, quiz_id)
    if quiz is None:
        return
    attempts = repo.attempts_for_quiz(conn, quiz_id)
    ungraded = [a for a in attempts if a["score"] is None]

    if ungraded:
        detail = failure or "grading did not complete"
        repo.set_status(
            conn,
            quiz_id,
            "graded",
            error=f"{len(ungraded)} of {len(attempts)} answers could not be graded: {detail}",
        )
        sessions_repo.set_summary(
            conn, quiz["session_id"], f"Quiz not scored: {quiz['title']} — {detail}"
        )
        return

    score = scoring.quiz_score([a["score"] for a in attempts])
    repo.set_status(conn, quiz_id, "graded", score=score)

    # Grouped by topic: a question may override the quiz's topic, and each
    # topic should only move on the evidence that actually measured it.
    by_topic: dict[int, list[dict]] = {}
    for attempt in attempts:
        by_topic.setdefault(attempt["topic_id"], []).append(attempt)

    for topic_id, topic_attempts in by_topic.items():
        previous = repo.get_understanding(conn, topic_id)
        if previous is None:
            continue  # topic deleted between submit and grading
        topic = materials_repo.get_topic(conn, topic_id)
        topic_score = scoring.quiz_score([a["score"] for a in topic_attempts])
        understanding = scoring.blend(previous, topic_score)
        repo.apply_understanding(
            conn,
            topic_id=topic_id,
            understanding=understanding,
            previous=previous,
            source="quiz",
            reason=scoring.reason(
                topic["name"], previous, understanding, topic_score, topic_attempts
            ),
            evidence=scoring.evidence(topic_score, previous, topic_attempts),
            quiz_id=quiz_id,
            session_id=quiz["session_id"],
        )

    sessions_repo.set_summary(
        conn, quiz["session_id"], f"Scored {score}/100 on {quiz['title']}"
    )
