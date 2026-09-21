"""Quiz round-trip: answer, submit, grade, move the topic's score.

The failure this file mostly guards against is a score nobody can justify --
either because the answer key leaked before submission, or because the
understanding number moved without a traceable cause.
"""

import os
import tempfile
from pathlib import Path

# Point the app at a throwaway DB before anything imports the settings cache.
_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "quizzes.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["WARM_EMBEDDINGS"] = "false"

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agent import hermes  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402

# What the fake grader awards, so a test can assert the arithmetic exactly.
OPEN_ENDED_SCORE = 80


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """A Hermes that grades every open-ended answer identically.

    Deterministic on purpose: these tests are about the plumbing around the
    grade -- the blend, the evidence row, the status transitions -- and a
    varying score would make every expected number a moving target.
    """

    async def fake_complete(prompt, *, system=None):
        fake_complete.prompts.append(prompt)
        return json.dumps(
            {"score": OPEN_ENDED_SCORE, "feedback": "Solid, but you skipped the sign convention.", "cited": [0]}
        )

    fake_complete.prompts = []
    monkeypatch.setattr(hermes, "complete", fake_complete)

    init_db()
    with connection() as conn:
        for table in ("quiz_attempts", "understanding_events", "quiz_questions",
                      "quizzes", "sessions", "chunks", "source_files", "topics"):
            conn.execute(f"DELETE FROM {table}")
    return fake_complete


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_topic(name="Entropy", understanding=None) -> int:
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO topics (name) VALUES (?) RETURNING id", (name,)
        )
        topic_id = cur.fetchone()["id"]
        if understanding is not None:
            conn.execute(
                "UPDATE topics SET user_understanding = ? WHERE id = ?",
                (understanding, topic_id),
            )
    return topic_id


def make_chunk(topic_id: int, text: str) -> int:
    with connection() as conn:
        cur = conn.execute(
            "INSERT INTO source_files (filename, upload_type) VALUES ('notes.txt','text') RETURNING id"
        )
        file_id = cur.fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO chunks (source_file_id, topic_id, text) VALUES (?, ?, ?) RETURNING id",
            (file_id, topic_id, text),
        )
        return cur.fetchone()["id"]


def quiz_payload(topic_id: int, **overrides) -> dict:
    payload = {
        "topic_id": topic_id,
        "title": "Entropy check-in",
        "resources": [],
        "questions": [
            {
                "kind": "multiple_choice",
                "prompt": "Entropy of an isolated system…",
                "options": ["decreases", "never decreases", "is constant"],
                "correct_option": 1,
                "explanation": "Second law.",
            },
            {
                "kind": "open_ended",
                "prompt": "Explain why ΔS > 0 for spontaneous processes.",
                "rubric": "Mentions irreversibility and the second law.",
            },
        ],
    }
    payload.update(overrides)
    return payload


def create_quiz(client, topic_id: int, **overrides) -> dict:
    resp = client.post("/quizzes", json=quiz_payload(topic_id, **overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


def topic_understanding(topic_id: int) -> int:
    with connection() as conn:
        return conn.execute(
            "SELECT user_understanding FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()[0]


# --------------------------------------------------------------------------
# creating
# --------------------------------------------------------------------------


class TestCreate:
    def test_creates_quiz_and_its_session_log_row(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)

        assert quiz["status"] == "ready"
        assert quiz["topic_name"] == "Entropy"
        assert [q["order_index"] for q in quiz["questions"]] == [0, 1]

        # A quiz that skipped the Sessions log would be invisible there forever.
        session = client.get(f"/sessions/{quiz['session_id']}").json()
        assert session["type"] == "quiz"
        assert session["payload"]["quiz_title"] == "Entropy check-in"

    def test_unknown_topic_is_404(self, client):
        assert client.post("/quizzes", json=quiz_payload(9999)).status_code == 404

    def test_multiple_choice_needs_a_key_in_range(self, client):
        topic_id = make_topic()
        bad = quiz_payload(topic_id)
        bad["questions"][0]["correct_option"] = 7
        assert client.post("/quizzes", json=bad).status_code == 422

    def test_open_ended_cannot_carry_options(self, client):
        topic_id = make_topic()
        bad = quiz_payload(topic_id)
        bad["questions"][1]["options"] = ["a", "b"]
        assert client.post("/quizzes", json=bad).status_code == 422

    def test_external_resource_without_an_excerpt_is_rejected(self, client):
        topic_id = make_topic()
        payload = quiz_payload(
            topic_id, resources=[{"kind": "external", "url": "https://example.org/x"}]
        )
        # Nothing can re-fetch it later, so it could never reach the grader.
        assert client.post("/quizzes", json=payload).status_code == 422


# --------------------------------------------------------------------------
# the answer key
# --------------------------------------------------------------------------


class TestAnswerKeyIsWithheld:
    def test_key_is_hidden_before_grading_and_shown_after(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)

        fetched = client.get(f"/quizzes/{quiz['id']}").json()
        mcq, open_ended = fetched["questions"]
        assert mcq["correct_option"] is None
        assert mcq["explanation"] is None
        assert open_ended["rubric"] is None
        # The options themselves are not secret -- only which one is right.
        assert mcq["options"] == ["decreases", "never decreases", "is constant"]

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 1}]},
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        graded = client.get(f"/quizzes/{quiz['id']}").json()
        assert graded["questions"][0]["correct_option"] == 1
        assert graded["questions"][1]["rubric"]


# --------------------------------------------------------------------------
# answering
# --------------------------------------------------------------------------


class TestAnswers:
    def test_partial_saves_accumulate_and_are_repeatable(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        mcq, open_ended = quiz["questions"]

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 0}]},
        )
        state = client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": open_ended["id"], "answer": "because irreversibility"}]},
        ).json()

        assert state["status"] == "in_progress"
        assert len(state["answers"]) == 2

        # Re-answering replaces rather than duplicating.
        state = client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 1}]},
        ).json()
        assert len(state["answers"]) == 2
        assert [a for a in state["answers"] if a["question_id"] == mcq["id"]][0][
            "selected_option"
        ] == 1

    def test_question_from_another_quiz_is_404(self, client):
        topic_id = make_topic()
        a = create_quiz(client, topic_id)
        b = create_quiz(client, topic_id)
        resp = client.post(
            f"/quizzes/{a['id']}/answers",
            json={"answers": [{"question_id": b["questions"][0]["id"], "selected_option": 0}]},
        )
        assert resp.status_code == 404

    def test_answers_are_rejected_once_graded(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        mcq = quiz["questions"][0]
        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 1}]},
        )
        client.post(f"/quizzes/{quiz['id']}/submit")
        resp = client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 0}]},
        )
        assert resp.status_code == 409


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------


class TestGrading:
    def test_full_round_trip_scores_both_kinds(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        mcq, open_ended = quiz["questions"]

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={
                "answers": [
                    {"question_id": mcq["id"], "selected_option": 1},
                    {"question_id": open_ended["id"], "answer": "Irreversible processes raise total entropy."},
                ]
            },
        )
        resp = client.post(f"/quizzes/{quiz['id']}/submit")
        # 202: the open-ended pass is a background task. TestClient runs it
        # before returning, so the stored quiz is already finished.
        assert resp.status_code == 202, resp.text

        graded = client.get(f"/quizzes/{quiz['id']}").json()
        assert graded["status"] == "graded"
        assert graded["grading_error"] is None
        # (100 + 80) / 2
        assert graded["score"] == (100 + OPEN_ENDED_SCORE) // 2

        by_question = {a["question_id"]: a for a in graded["answers"]}
        assert by_question[mcq["id"]]["graded_by"] == "key"
        assert by_question[mcq["id"]]["score"] == 100
        assert by_question[open_ended["id"]]["graded_by"] == "hermes"
        assert by_question[open_ended["id"]]["feedback"]

    def test_multiple_choice_only_quiz_grades_inline(self, client, stubs):
        topic_id = make_topic()
        payload = quiz_payload(topic_id)
        payload["questions"] = [payload["questions"][0]]
        quiz = client.post("/quizzes", json=payload).json()

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": quiz["questions"][0]["id"], "selected_option": 1}]},
        )
        resp = client.post(f"/quizzes/{quiz['id']}/submit")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "graded"
        assert resp.json()["score"] == 100
        # No key-graded question should ever cost a model call.
        assert stubs.prompts == []

    def test_unanswered_questions_count_as_wrong(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        mcq = quiz["questions"][0]

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": mcq["id"], "selected_option": 1}]},
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        graded = client.get(f"/quizzes/{quiz['id']}").json()
        # Skipping the second question must not score the quiz 100/100.
        assert len(graded["answers"]) == 2
        assert graded["score"] < 100

    def test_submit_without_any_answer_is_409(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        assert client.post(f"/quizzes/{quiz['id']}/submit").status_code == 409

    def test_grader_sees_the_resources_the_quiz_was_built_from(self, client, stubs):
        topic_id = make_topic()
        chunk_id = make_chunk(topic_id, "Entropy never decreases in an isolated system.")
        quiz = create_quiz(
            client,
            topic_id,
            resources=[
                {"kind": "chunk", "chunk_id": chunk_id},
                {
                    "kind": "external",
                    "url": "https://example.org/thermo",
                    "title": "Thermo notes",
                    "text": "Spontaneity is governed by the second law.",
                },
            ],
        )
        open_ended = quiz["questions"][1]
        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": open_ended["id"], "answer": "entropy rises"}]},
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        prompt = stubs.prompts[-1]
        # Chunk text is rehydrated from SQLite, external text from the snapshot.
        assert "Entropy never decreases in an isolated system." in prompt
        assert "Spontaneity is governed by the second law." in prompt
        assert "Mentions irreversibility and the second law." in prompt  # the rubric

        # The cited resource is stored as the evidence for that one grade.
        graded = client.get(f"/quizzes/{quiz['id']}").json()
        cited = [a for a in graded["answers"] if a["question_id"] == open_ended["id"]][0][
            "grading_resources"
        ]
        assert cited and cited[0]["chunk_id"] == chunk_id

    def test_hermes_failure_leaves_the_quiz_unscored(self, client, monkeypatch):
        topic_id = make_topic(understanding=50)
        quiz = create_quiz(client, topic_id)
        mcq, open_ended = quiz["questions"]

        async def boom(prompt, *, system=None):
            raise hermes.HermesError("connection refused")

        monkeypatch.setattr(hermes, "complete", boom)

        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={
                "answers": [
                    {"question_id": mcq["id"], "selected_option": 1},
                    {"question_id": open_ended["id"], "answer": "something"},
                ]
            },
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        graded = client.get(f"/quizzes/{quiz['id']}").json()
        assert graded["status"] == "graded"
        assert graded["grading_error"]
        # Scoring on the multiple-choice half alone would inflate the topic
        # every time the gateway was down.
        assert graded["score"] is None
        assert topic_understanding(topic_id) == 50


# --------------------------------------------------------------------------
# understanding + evidence
# --------------------------------------------------------------------------


class TestUnderstanding:
    def test_first_quiz_sets_the_score_and_logs_why(self, client):
        topic_id = make_topic()
        assert topic_understanding(topic_id) == -1

        quiz = create_quiz(client, topic_id)
        mcq, open_ended = quiz["questions"]
        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={
                "answers": [
                    {"question_id": mcq["id"], "selected_option": 1},
                    {"question_id": open_ended["id"], "answer": "entropy rises"},
                ]
            },
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        expected = (100 + OPEN_ENDED_SCORE) // 2
        # No history to blend against, so the first signal is taken as-is.
        assert topic_understanding(topic_id) == expected

        events = client.get(f"/quizzes/evidence/{topic_id}").json()
        assert len(events) == 1
        event = events[0]
        assert event["previous_understanding"] == -1
        assert event["understanding"] == expected
        assert event["quiz_id"] == quiz["id"]
        assert str(expected) in event["reason"]
        assert event["evidence"]["quiz_score"] == expected
        assert len(event["evidence"]["questions"]) == 2

    def test_later_quiz_blends_against_the_existing_score(self, client):
        topic_id = make_topic(understanding=40)
        quiz = create_quiz(client, topic_id)
        mcq, open_ended = quiz["questions"]
        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={
                "answers": [
                    {"question_id": mcq["id"], "selected_option": 1},
                    {"question_id": open_ended["id"], "answer": "entropy rises"},
                ]
            },
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        quiz_score = (100 + OPEN_ENDED_SCORE) // 2
        # 40 * 0.4 + 90 * 0.6 -- one quiz moves the score, it does not replace it.
        assert topic_understanding(topic_id) == round(40 * 0.4 + quiz_score * 0.6)

    def test_evidence_survives_the_quiz_being_deleted(self, client):
        topic_id = make_topic()
        quiz = create_quiz(client, topic_id)
        client.post(
            f"/quizzes/{quiz['id']}/answers",
            json={"answers": [{"question_id": quiz["questions"][0]["id"], "selected_option": 1}]},
        )
        client.post(f"/quizzes/{quiz['id']}/submit")

        assert client.delete(f"/quizzes/{quiz['id']}").status_code == 204
        assert client.get(f"/quizzes/{quiz['id']}").status_code == 404
        # The log row goes with it...
        assert client.get(f"/sessions/{quiz['session_id']}").status_code == 404

        # ...but deleting a quiz must not rewrite the history of a score it caused.
        events = client.get(f"/quizzes/evidence/{topic_id}").json()
        assert len(events) == 1
        assert events[0]["quiz_id"] is None
        assert events[0]["reason"]

    def test_evidence_for_unknown_topic_is_404(self, client):
        assert client.get("/quizzes/evidence/9999").status_code == 404


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


class TestListing:
    def test_filters_and_paginates(self, client):
        entropy = make_topic("Entropy")
        enthalpy = make_topic("Enthalpy")
        create_quiz(client, entropy)
        create_quiz(client, entropy)
        create_quiz(client, enthalpy)

        everything = client.get("/quizzes").json()
        assert everything["total"] == 3
        assert everything["items"][0]["question_count"] == 2

        scoped = client.get("/quizzes", params={"topic_id": enthalpy}).json()
        assert scoped["total"] == 1
        assert scoped["items"][0]["topic_name"] == "Enthalpy"

        assert client.get("/quizzes", params={"status": "graded"}).json()["total"] == 0

        page = client.get("/quizzes", params={"limit": 2}).json()
        ids = [q["id"] for q in page["items"]]
        assert len(ids) == 2 and ids == sorted(ids, reverse=True)

    def test_unknown_quiz_is_404(self, client):
        assert client.get("/quizzes/9999").status_code == 404
