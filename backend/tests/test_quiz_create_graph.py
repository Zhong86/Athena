"""Full quiz-creation round-trip: topic pick in, committed `quiz_id` out.

Hermes is stubbed; SQLite and the checkpointer are real. Mirrors
`test_goal_graph.py`: the interrupts are the point of this test -- a graph
that cannot be resumed after a reload is useless to the UI regardless of how
good the questions are.
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "quiz_create_graph.db")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.db import connection, init_db  # noqa: E402
from app.quizzes import graph as graph_module  # noqa: E402
from app.quizzes import repository as repo  # noqa: E402
from app.quizzes.state import new_state  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM quizzes")
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM topics")
        conn.execute("DELETE FROM source_files")
    yield


@pytest.fixture
def app_graph():
    return graph_module.build().compile(checkpointer=InMemorySaver())


def _seed_topic(name: str = "Entropy", n: int = 3) -> int:
    with connection() as conn:
        topic = conn.execute(
            "INSERT INTO topics (name) VALUES (?) RETURNING id", (name,)
        ).fetchone()
        source = conn.execute(
            "INSERT INTO source_files (filename, upload_type) VALUES ('notes.txt', 'text') "
            "RETURNING id"
        ).fetchone()
        for i in range(n):
            conn.execute(
                "INSERT INTO chunks (source_file_id, topic_id, text, order_index) "
                "VALUES (?, ?, ?, ?)",
                (source["id"], topic["id"], f"Entropy fact {i}: disorder increases.", i),
            )
        return topic["id"]


GENERATION_REPLY = {
    "title": "Entropy check-in",
    "questions": [
        {
            "kind": "multiple_choice",
            "prompt": "What does entropy measure?",
            "options": ["Disorder", "Mass", "Speed", "Charge"],
            "correct_option": 0,
            "explanation": "Entropy measures disorder.",
            "source_excerpts": [0, 1],
        },
        {
            "kind": "multiple_choice",
            "prompt": "Entropy of an isolated system tends to?",
            "options": ["Decrease", "Stay fixed", "Increase", "Oscillate"],
            "correct_option": 2,
            "explanation": "Second law of thermodynamics.",
            "source_excerpts": [2],
        },
    ],
}


@pytest.fixture
def fake_hermes(monkeypatch):
    calls = {"n": 0}

    async def complete(prompt: str, *, system: str | None = None) -> str:
        calls["n"] += 1
        return json.dumps(GENERATION_REPLY)

    monkeypatch.setattr("agent.hermes.complete", complete)
    return calls


def run(app_graph, thread_id: str, payload):
    config = {"configurable": {"thread_id": thread_id}}
    return app_graph.invoke(payload, config=config)


def interrupt_of(result) -> dict:
    assert result.get("__interrupt__"), f"expected an interrupt, got {list(result)}"
    return result["__interrupt__"][0].value


def test_full_round_trip(app_graph, fake_hermes):
    topic_id = _seed_topic()

    state = run(app_graph, "q1", new_state())
    choose_topic = interrupt_of(state)
    assert choose_topic["kind"] == "choose_topic"
    assert choose_topic["topics"][0]["name"] == "Entropy"

    state = run(app_graph, "q1", Command(resume={"topic_id": topic_id}))
    choose_format = interrupt_of(state)
    assert choose_format["kind"] == "choose_format"
    assert choose_format["topic_name"] == "Entropy"

    state = run(app_graph, "q1", Command(resume={"format": "multiple_choice", "count": 2}))
    review = interrupt_of(state)
    assert review["kind"] == "review"
    assert review["quiz_title"] == "Entropy check-in"
    assert len(review["questions"]) == 2
    assert fake_hermes["n"] == 1

    final = run(app_graph, "q1", Command(resume="start"))
    assert final["status"] == "committed"
    quiz_id = final["quiz_id"]
    assert quiz_id

    with connection() as conn:
        quiz = repo.get_quiz(conn, quiz_id)
        questions = repo.questions_for_quiz(conn, quiz_id)

    assert quiz["title"] == "Entropy check-in"
    assert quiz["topic_id"] == topic_id
    assert quiz["status"] == "ready"
    assert len(questions) == 2
    assert questions[0]["correct_option"] == 0


def test_invalid_topic_reprompts_without_reasking_hermes(app_graph, fake_hermes):
    _seed_topic("Entropy")
    run(app_graph, "q2", new_state())
    state = run(app_graph, "q2", Command(resume={"topic_name": "Does Not Exist"}))
    choose_topic = interrupt_of(state)
    assert choose_topic["kind"] == "choose_topic"
    assert choose_topic["error"]
    assert fake_hermes["n"] == 0  # never reached generation


def test_regenerate_calls_hermes_again(app_graph, fake_hermes):
    topic_id = _seed_topic()
    run(app_graph, "q3", new_state())
    run(app_graph, "q3", Command(resume={"topic_id": topic_id}))
    run(app_graph, "q3", Command(resume={"format": "multiple_choice", "count": 2}))
    assert fake_hermes["n"] == 1

    state = run(app_graph, "q3", Command(resume={"action": "regenerate"}))
    review = interrupt_of(state)
    assert review["kind"] == "review"
    assert fake_hermes["n"] == 2

    with connection() as conn:
        assert repo.count_quizzes(conn) == 0  # nothing committed yet


def test_cancel_abandons_without_committing(app_graph, fake_hermes):
    topic_id = _seed_topic()
    run(app_graph, "q4", new_state())
    run(app_graph, "q4", Command(resume={"topic_id": topic_id}))
    run(app_graph, "q4", Command(resume={"format": "open_ended", "count": 2}))

    final = run(app_graph, "q4", Command(resume={"action": "cancel"}))
    assert final["status"] == "abandoned"
    assert "quiz_id" not in final

    with connection() as conn:
        assert repo.count_quizzes(conn) == 0


def test_no_materials_raises(app_graph):
    with pytest.raises(Exception):
        run(app_graph, "q5", new_state())
