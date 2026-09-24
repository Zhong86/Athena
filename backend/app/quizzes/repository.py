"""SQL for `quizzes`, `quiz_questions`, `quiz_attempts`, `understanding_events`
and `quiz_creation_runs`.

Same contract the other repositories keep: every function takes the caller's
connection and never opens its own, so a submit that writes attempt rows, the
quiz score, the topic's understanding and the evidence row either lands whole or
not at all. A quiz whose score moved a topic but whose evidence row is missing is
exactly the state Step 4 exists to prevent.
"""

import json
import sqlite3
from typing import Any

from app.clock import utc_now_iso

# topics.user_understanding's "no signal yet" sentinel. Imported rather than
# re-declared: two copies of -1 that drift is a silent wrong answer.
from app.ranking import NO_SIGNAL

QUIZ_STATUSES = ("ready", "in_progress", "grading", "graded")
# Exactly 011's CHECK on quiz_creation_runs.status.
RUN_STATUSES = (
    "choosing_topic",
    "choosing_format",
    "generating",
    "reviewing",
    "committed",
    "abandoned",
)


def _json_list(raw: Any) -> list:
    """Tolerant decode: a malformed JSON column costs the resources on one row,
    never the request. Matches the goals repository's handling."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _json_obj(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _quiz_row(row: sqlite3.Row) -> dict[str, Any]:
    quiz = dict(row)
    quiz["resources"] = _json_list(quiz.get("resources"))
    return quiz


def _question_row(row: sqlite3.Row) -> dict[str, Any]:
    question = dict(row)
    question["options"] = _json_list(question.get("options"))
    question["resources"] = _json_list(question.get("resources"))
    return question


def _attempt_row(row: sqlite3.Row) -> dict[str, Any]:
    attempt = dict(row)
    attempt["grading_resources"] = _json_list(attempt.get("grading_resources"))
    # SQLite has no boolean; 001 stores 0/1 and NULL means "not graded yet".
    attempt["correct"] = None if attempt["correct"] is None else bool(attempt["correct"])
    return attempt


# --------------------------------------------------------------------------
# quizzes
# --------------------------------------------------------------------------


def create_quiz(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    topic_id: int,
    title: str,
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO quizzes (session_id, topic_id, title, resources)
        VALUES (?, ?, ?, ?)
        RETURNING *
        """,
        (session_id, topic_id, title, json.dumps(resources)),
    )
    return _quiz_row(cur.fetchone())


def get_quiz(conn: sqlite3.Connection, quiz_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT q.*, t.name AS topic_name
        FROM quizzes q
        LEFT JOIN topics t ON t.id = q.topic_id
        WHERE q.id = ?
        """,
        (quiz_id,),
    ).fetchone()
    return _quiz_row(row) if row else None


def list_quizzes(
    conn: sqlite3.Connection,
    *,
    topic_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Newest first, matching the Sessions log's ordering."""
    sql = """
        SELECT q.*, t.name AS topic_name,
               (SELECT COUNT(*) FROM quiz_questions qq WHERE qq.quiz_id = q.id)
                   AS question_count
        FROM quizzes q
        LEFT JOIN topics t ON t.id = q.topic_id
    """
    where, params = [], []
    if topic_id is not None:
        where.append("q.topic_id = ?")
        params.append(topic_id)
    if status:
        where.append("q.status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY q.created_at DESC, q.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_quiz_row(r) for r in conn.execute(sql, params)]


def count_quizzes(
    conn: sqlite3.Connection,
    *,
    topic_id: int | None = None,
    status: str | None = None,
) -> int:
    sql = "SELECT COUNT(*) FROM quizzes"
    where, params = [], []
    if topic_id is not None:
        where.append("topic_id = ?")
        params.append(topic_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, params).fetchone()[0]


def set_status(
    conn: sqlite3.Connection,
    quiz_id: int,
    status: str,
    *,
    score: int | None = None,
    error: str | None = None,
    submitted: bool = False,
) -> None:
    """Clears any previous grading_error unless a new one is supplied, so a
    successful re-submit does not leave a stale message on the row."""
    sets = ["status = ?", "grading_error = ?"]
    params: list[Any] = [status, error]
    if score is not None:
        sets.append("score = ?")
        params.append(score)
    if submitted:
        sets.append("submitted_at = ?")
        params.append(utc_now_iso())
    if status == "graded":
        sets.append("graded_at = ?")
        params.append(utc_now_iso())
    params.append(quiz_id)
    conn.execute(f"UPDATE quizzes SET {', '.join(sets)} WHERE id = ?", params)


def delete_quiz(conn: sqlite3.Connection, quiz_id: int) -> int | None:
    """Returns the deleted quiz's session_id so the caller can remove the log
    row too. Questions and attempts go via ON DELETE CASCADE; understanding
    events survive with a NULL quiz_id, by design."""
    row = conn.execute(
        "SELECT session_id FROM quizzes WHERE id = ?", (quiz_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
    return row["session_id"]


# --------------------------------------------------------------------------
# questions
# --------------------------------------------------------------------------


def add_questions(
    conn: sqlite3.Connection, quiz_id: int, questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """order_index is assigned from list position, not taken from the payload.

    The generator hands over an ordered list; trusting it to also number that
    list correctly buys nothing and risks a UNIQUE(quiz_id, order_index)
    violation that reads as a server error rather than a client mistake.
    """
    rows = []
    for order_index, q in enumerate(questions):
        cur = conn.execute(
            """
            INSERT INTO quiz_questions
                (quiz_id, order_index, kind, prompt, options, correct_option,
                 rubric, explanation, resources, topic_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                quiz_id,
                order_index,
                q["kind"],
                q["prompt"],
                json.dumps(q.get("options") or []),
                q.get("correct_option"),
                q.get("rubric"),
                q.get("explanation"),
                json.dumps(q.get("resources") or []),
                q.get("topic_id"),
            ),
        )
        rows.append(_question_row(cur.fetchone()))
    return rows


def questions_for_quiz(conn: sqlite3.Connection, quiz_id: int) -> list[dict[str, Any]]:
    return [
        _question_row(r)
        for r in conn.execute(
            "SELECT * FROM quiz_questions WHERE quiz_id = ? ORDER BY order_index",
            (quiz_id,),
        )
    ]


# --------------------------------------------------------------------------
# attempts (answers)
# --------------------------------------------------------------------------


def save_answer(
    conn: sqlite3.Connection,
    *,
    quiz_id: int,
    session_id: int,
    question: dict[str, Any],
    topic_id: int,
    answer: str | None,
    selected_option: int | None,
) -> dict[str, Any]:
    """Write one answer, replacing any previous answer to the same question.

    Delete-then-insert rather than an UPDATE so that re-answering also clears
    the grade from the earlier attempt: a stale score sitting next to a changed
    answer is worse than no score at all.

    `question` text is snapshotted into the row because 001 declared that column
    NOT NULL for a reason -- the Sessions log still has to read correctly after
    the quiz itself is deleted.
    """
    conn.execute(
        "DELETE FROM quiz_attempts WHERE quiz_id = ? AND question_id = ?",
        (quiz_id, question["id"]),
    )
    cur = conn.execute(
        """
        INSERT INTO quiz_attempts
            (session_id, topic_id, question, answer, timestamp,
             quiz_id, question_id, kind, selected_option)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING *
        """,
        (
            session_id,
            topic_id,
            question["prompt"],
            answer,
            utc_now_iso(),
            quiz_id,
            question["id"],
            question["kind"],
            selected_option,
        ),
    )
    return _attempt_row(cur.fetchone())


def attempts_for_quiz(conn: sqlite3.Connection, quiz_id: int) -> list[dict[str, Any]]:
    return [
        _attempt_row(r)
        for r in conn.execute(
            """
            SELECT a.* FROM quiz_attempts a
            JOIN quiz_questions q ON q.id = a.question_id
            WHERE a.quiz_id = ?
            ORDER BY q.order_index
            """,
            (quiz_id,),
        )
    ]


def record_grade(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    score: int,
    correct: bool,
    feedback: str | None,
    graded_by: str,
    resources: list[dict[str, Any]],
) -> None:
    conn.execute(
        """
        UPDATE quiz_attempts
           SET score = ?, correct = ?, feedback = ?, graded_by = ?,
               grading_resources = ?, graded_at = ?
         WHERE id = ?
        """,
        (
            score,
            1 if correct else 0,
            feedback,
            graded_by,
            json.dumps(resources),
            utc_now_iso(),
            attempt_id,
        ),
    )


# --------------------------------------------------------------------------
# understanding + its evidence
# --------------------------------------------------------------------------


def get_understanding(conn: sqlite3.Connection, topic_id: int) -> int | None:
    row = conn.execute(
        "SELECT user_understanding FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    return None if row is None else int(row["user_understanding"])


def apply_understanding(
    conn: sqlite3.Connection,
    *,
    topic_id: int,
    understanding: int,
    previous: int,
    source: str,
    reason: str,
    evidence: dict[str, Any],
    quiz_id: int | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    """Move a topic's score and log why, in one transaction.

    These two writes are a single function rather than two callable steps
    precisely so no future caller can do one without the other.
    """
    conn.execute(
        "UPDATE topics SET user_understanding = ? WHERE id = ?",
        (understanding, topic_id),
    )
    cur = conn.execute(
        """
        INSERT INTO understanding_events
            (topic_id, source, quiz_id, session_id, previous_understanding,
             understanding, reason, evidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING *
        """,
        (
            topic_id,
            source,
            quiz_id,
            session_id,
            previous,
            understanding,
            reason,
            json.dumps(evidence),
            utc_now_iso(),
        ),
    )
    event = dict(cur.fetchone())
    event["evidence"] = _json_obj(event["evidence"])
    return event


def understanding_events(
    conn: sqlite3.Connection, topic_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    """The "why this score" history for one topic, newest first."""
    rows = conn.execute(
        """
        SELECT * FROM understanding_events
        WHERE topic_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (topic_id, limit),
    )
    events = []
    for r in rows:
        event = dict(r)
        event["evidence"] = _json_obj(event["evidence"])
        events.append(event)
    return events


def latest_understanding_event(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The single most recent score change, across every topic. Dashboard's
    Last Check-in card -- None means no quiz/session has ever scored anything."""
    row = conn.execute(
        """
        SELECT ue.*, t.name AS topic_name
        FROM understanding_events ue
        JOIN topics t ON t.id = ue.topic_id
        ORDER BY ue.created_at DESC, ue.id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    event = dict(row)
    event["evidence"] = _json_obj(event["evidence"])
    return event


# --------------------------------------------------------------------------
# quiz_creation_runs -- the index the HTTP layer looks in-flight graph runs up by
# --------------------------------------------------------------------------


def create_creation_run(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    topic_hint: str | None = None,
    status: str = "choosing_topic",
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO quiz_creation_runs (thread_id, topic_hint, status) VALUES (?, ?, ?) "
        "RETURNING *",
        (thread_id, topic_hint, status),
    )
    return dict(cur.fetchone())


def get_creation_run(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM quiz_creation_runs WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return dict(row) if row else None


def list_unfinished_creation_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Runs the student can still come back to -- same reasoning as
    `app.goals.repository.list_unfinished_runs`."""
    rows = conn.execute(
        "SELECT * FROM quiz_creation_runs WHERE status NOT IN ('committed', 'abandoned') "
        "ORDER BY updated_at DESC, thread_id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def set_creation_run_status(
    conn: sqlite3.Connection, thread_id: str, status: str, *, quiz_id: int | None = None
) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run status: {status}")
    conn.execute(
        "UPDATE quiz_creation_runs SET status = ?, updated_at = ?, "
        "quiz_id = COALESCE(?, quiz_id) WHERE thread_id = ?",
        (status, utc_now_iso(), quiz_id, thread_id),
    )
