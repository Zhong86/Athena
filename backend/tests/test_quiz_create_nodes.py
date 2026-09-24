"""Per-node behaviour, called directly rather than through the graph.

Mirrors `test_goal_nodes.py`: the graph test covers the happy path end to end;
these cover the branches it cannot reach cheaply -- an empty Materials table, a
dead gateway, and the deterministic resolve/format-parsing rules.
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "quiz_create_nodes.db")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402

from agent import hermes  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.quizzes.llm import LLMUnavailable  # noqa: E402
from app.quizzes.nodes.choose_format import choose_format  # noqa: E402
from app.quizzes.nodes.choose_topic import choose_topic  # noqa: E402
from app.quizzes.nodes.generate import generate_questions  # noqa: E402
from app.quizzes.nodes.review import present_quiz  # noqa: E402
from app.quizzes.state import NoMaterials, new_state  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM topics")
        conn.execute("DELETE FROM source_files")
    yield


@pytest.fixture
def hermes_says(monkeypatch):
    def install(payload):
        async def complete(prompt: str, *, system: str | None = None) -> str:
            return payload if isinstance(payload, str) else json.dumps(payload)

        monkeypatch.setattr("agent.hermes.complete", complete)

    return install


@pytest.fixture
def hermes_dead(monkeypatch):
    async def complete(prompt: str, *, system: str | None = None) -> str:
        raise hermes.HermesError("connection refused")

    monkeypatch.setattr("agent.hermes.complete", complete)


def _seed_topic_with_chunks(name: str = "Entropy", n: int = 3) -> int:
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


# --------------------------------------------------------------------------
# choose_topic
# --------------------------------------------------------------------------


def test_choose_topic_raises_without_any_material():
    """Nothing to fall back on: a quiz has to be grounded in something."""
    with pytest.raises(NoMaterials):
        choose_topic(new_state())


def test_choose_topic_resolves_by_id(monkeypatch):
    topic_id = _seed_topic_with_chunks("Entropy")

    def fake_interrupt(payload):
        assert payload["kind"] == "choose_topic"
        assert payload["topics"][0]["name"] == "Entropy"
        return {"topic_id": topic_id}

    monkeypatch.setattr("app.quizzes.nodes.choose_topic.interrupt", fake_interrupt)
    result = choose_topic(new_state())
    assert result["topic_id"] == topic_id
    assert result["topic_name"] == "Entropy"
    assert result["status"] == "choosing_format"


def test_choose_topic_resolves_by_fuzzy_name(monkeypatch):
    topic_id = _seed_topic_with_chunks("Thermodynamics")

    monkeypatch.setattr(
        "app.quizzes.nodes.choose_topic.interrupt", lambda payload: "thermo"
    )
    result = choose_topic(new_state())
    assert result["topic_id"] == topic_id


def test_choose_topic_reprompts_on_unresolvable_answer(monkeypatch):
    _seed_topic_with_chunks("Entropy")
    monkeypatch.setattr(
        "app.quizzes.nodes.choose_topic.interrupt", lambda payload: {"topic_name": "Nonsense"}
    )
    result = choose_topic(new_state())
    assert result["status"] == "choosing_topic"
    assert "topic_error" in result
    assert "topic_id" not in result


# --------------------------------------------------------------------------
# choose_format
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("multiple choice", "multiple_choice"),
        ("mc", "multiple_choice"),
        ("open-ended", "open_ended"),
        ("essay", "open_ended"),
        ("both", "mixed"),
    ],
)
def test_choose_format_resolves_aliases(monkeypatch, raw, expected):
    monkeypatch.setattr(
        "app.quizzes.nodes.choose_format.interrupt", lambda payload: {"format": raw}
    )
    result = choose_format(new_state())
    assert result["question_format"] == expected
    assert result["status"] == "generating"


def test_choose_format_clamps_count(monkeypatch):
    monkeypatch.setattr(
        "app.quizzes.nodes.choose_format.interrupt",
        lambda payload: {"format": "mixed", "count": 999},
    )
    result = choose_format(new_state())
    assert result["question_count"] == 10  # MAX_QUESTIONS


def test_choose_format_reprompts_on_unknown_format(monkeypatch):
    monkeypatch.setattr(
        "app.quizzes.nodes.choose_format.interrupt", lambda payload: {"format": "gibberish"}
    )
    result = choose_format(new_state())
    assert result["status"] == "choosing_format"
    assert "format_error" in result


# --------------------------------------------------------------------------
# generate_questions
# --------------------------------------------------------------------------


def _gen_state(topic_id: int, fmt: str = "multiple_choice", count: int = 3) -> dict:
    state = new_state()
    state["topic_id"] = topic_id
    state["topic_name"] = "Entropy"
    state["question_format"] = fmt
    state["question_count"] = count
    return state


def test_generate_produces_grounded_multiple_choice(hermes_says):
    topic_id = _seed_topic_with_chunks("Entropy", n=2)
    hermes_says(
        {
            "title": "Entropy check-in",
            "questions": [
                {
                    "kind": "multiple_choice",
                    "prompt": "What does entropy measure?",
                    "options": ["Disorder", "Mass", "Speed", "Charge"],
                    "correct_option": 0,
                    "explanation": "Entropy measures disorder.",
                    "source_excerpts": [0, 1],
                }
            ],
        }
    )
    result = generate_questions(_gen_state(topic_id, count=1))
    assert result["status"] == "reviewing"
    assert result["quiz_title"] == "Entropy check-in"
    q = result["draft_questions"][0]
    assert q["kind"] == "multiple_choice"
    assert q["correct_option"] == 0
    assert len(q["resources"]) == 2
    assert result["resources"]  # quiz-level resources aggregated


def test_generate_drops_malformed_questions_but_keeps_the_rest(hermes_says):
    topic_id = _seed_topic_with_chunks("Entropy")
    hermes_says(
        {
            "title": "Quiz",
            "questions": [
                {"kind": "multiple_choice", "prompt": "Bad: no options", "options": []},
                {
                    "kind": "open_ended",
                    "prompt": "Explain entropy.",
                    "rubric": "Mentions disorder.",
                    "explanation": "See notes.",
                    "source_excerpts": [0],
                },
            ],
        }
    )
    result = generate_questions(_gen_state(topic_id, fmt="mixed", count=2))
    assert len(result["draft_questions"]) == 1
    assert result["draft_questions"][0]["kind"] == "open_ended"


def test_generate_raises_when_hermes_is_down(hermes_dead):
    topic_id = _seed_topic_with_chunks("Entropy")
    with pytest.raises(LLMUnavailable):
        generate_questions(_gen_state(topic_id))


def test_generate_raises_without_material():
    with pytest.raises(NoMaterials):
        generate_questions(_gen_state(topic_id=999999))


def test_generate_raises_when_nothing_usable_comes_back(hermes_says):
    topic_id = _seed_topic_with_chunks("Entropy")
    hermes_says({"title": "Quiz", "questions": [{"kind": "multiple_choice", "prompt": ""}]})
    with pytest.raises(LLMUnavailable):
        generate_questions(_gen_state(topic_id))


# --------------------------------------------------------------------------
# present_quiz
# --------------------------------------------------------------------------


def test_present_quiz_start_sets_ready_to_commit(monkeypatch):
    monkeypatch.setattr("app.quizzes.nodes.review.interrupt", lambda payload: "start")
    result = present_quiz(new_state())
    assert result["ready_to_commit"] is True
    assert result["status"] == "reviewing"


def test_present_quiz_regenerate_loops_back(monkeypatch):
    monkeypatch.setattr(
        "app.quizzes.nodes.review.interrupt", lambda payload: {"action": "regenerate"}
    )
    state = new_state()
    state["draft_questions"] = [{"kind": "multiple_choice"}]
    result = present_quiz(state)
    assert result["status"] == "generating"
    assert result["draft_questions"] == []


def test_present_quiz_cancel_abandons(monkeypatch):
    monkeypatch.setattr("app.quizzes.nodes.review.interrupt", lambda payload: "cancel")
    result = present_quiz(new_state())
    assert result["status"] == "abandoned"


def test_present_quiz_reprompts_on_unknown_action(monkeypatch):
    monkeypatch.setattr("app.quizzes.nodes.review.interrupt", lambda payload: "whatever")
    result = present_quiz(new_state())
    assert result["status"] == "reviewing"
    assert result["ready_to_commit"] is False
    assert "review_error" in result
