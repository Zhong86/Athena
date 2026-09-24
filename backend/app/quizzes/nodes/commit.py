"""`commit_quiz` -- persist the reviewed draft and hand back a `quiz_id`.

Reuses `app.quizzes.repository`'s own primitives -- the same ones `POST
/quizzes` uses -- so a generated quiz lands in exactly the same shape a
hand-built one would, and the rest of the quiz lifecycle (answering, grading)
needs no branch for "did an agent make this."
"""

import logging
from typing import Any

from app.db import connection
from app.quizzes import repository as repo
from app.quizzes.state import QuizDraftState
from app.sessions import repository as sessions_repo

log = logging.getLogger(__name__)


def commit_quiz(state: QuizDraftState) -> dict[str, Any]:
    title = state.get("quiz_title") or f"{state.get('topic_name', 'Untitled')} quiz"
    resources = state.get("resources") or []
    questions = state.get("draft_questions") or []

    with connection() as conn:
        session = sessions_repo.create(
            conn,
            type="quiz",
            payload={"quiz_title": title, "topic_id": state["topic_id"]},
            summary=f"Quiz ready: {title}",
        )
        quiz = repo.create_quiz(
            conn,
            session_id=session["id"],
            topic_id=state["topic_id"],
            title=title,
            resources=resources,
        )
        repo.add_questions(conn, quiz["id"], questions)

    log.info("quiz create: committed quiz %s with %d questions", quiz["id"], len(questions))
    return {"quiz_id": quiz["id"], "status": "committed"}
