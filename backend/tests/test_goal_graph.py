"""Full roadmap round-trip: goal string in, committed `goal_id` out.

Hermes is stubbed; SQLite and the checkpointer are real. The interrupts are the
point of this test -- a graph that cannot be resumed after a reload is useless to
the UI regardless of how good its milestones are.
"""

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "graph.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from app.db import connection, init_db  # noqa: E402
from app.goals import graph as graph_module  # noqa: E402
from app.goals import repository as repo  # noqa: E402
from app.goals.state import MAX_CLARIFY_TURNS, new_state  # noqa: E402

GOAL = "Want to pass the Thermo midterm"


@pytest.fixture(autouse=True)
def database():
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM topics")
    yield


@pytest.fixture
def app_graph():
    """Compiled against an in-memory checkpointer, so each test starts clean."""
    return graph_module.build().compile(checkpointer=InMemorySaver())


class FakeHermes:
    """Answers each roadmap prompt by recognising what it asks for.

    Deliberately shape-matching rather than a canned sequence: a node that stops
    calling Hermes, or calls it a different number of times, should not quietly
    keep passing.
    """

    def __init__(self, *, clarify_rounds: int = 1):
        self.clarify_rounds = clarify_rounds
        self.clarify_calls = 0
        self.copy_calls = 0
        self.searches = 0

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        if "Decide whether this goal is specific enough" in prompt:
            self.clarify_calls += 1
            if self.clarify_calls <= self.clarify_rounds:
                return json.dumps(
                    {
                        "needs_clarification": True,
                        "questions": ["Which course, and when is the exam?"],
                        "suggested_answers": [["CHEM 2010, Oct 3", "Not sure yet"]],
                        "clarified_goal": None,
                        "extracted": {},
                    }
                )
            return json.dumps(
                {
                    "needs_clarification": False,
                    "questions": [],
                    "clarified_goal": "Be ready for the CHEM 2010 thermodynamics midterm on Oct 3",
                    "extracted": {
                        "short_name": "Thermo midterm",
                        "course_code": "CHEM 2010",
                        "category": "academic",
                        "due_at": "2026-10-03",
                        "derivation": "Derived from your uploaded lecture notes",
                    },
                }
            )

        if "Break this into" in prompt:
            return json.dumps(
                {
                    "milestones": [
                        {"title": "Review thermodynamics fundamentals", "description": "Refresher."},
                        {"title": "Rebuild entropy from the ground up", "description": "Derivations."},
                        {"title": "Second-law problem sets", "description": "Practice."},
                    ]
                }
            )

        if "Write the two explanations" in prompt:
            self.copy_calls += 1
            title = re.search(r'Milestone: "(.*?)"', prompt).group(1)
            return json.dumps(
                {
                    "reason": f"short reason for {title}",
                    "reason_long": f"long reason for {title}",
                    "est_effort_min": 45,
                    "est_effort_max": 60,
                }
            )

        raise AssertionError(f"unexpected prompt: {prompt[:200]}")


@pytest.fixture
def fake_hermes(monkeypatch):
    fake = FakeHermes()

    async def complete(prompt: str, *, system: str | None = None) -> str:
        return fake.complete(prompt, system=system)

    monkeypatch.setattr("agent.hermes.complete", complete)
    return fake


@pytest.fixture
def no_materials(monkeypatch):
    """Nothing uploaded: every milestone should take the research branch."""

    def search(topic, query, *, limit=5):
        return {"topic_resolved": None, "topic_matched": False, "results": []}

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", search)
    return search


def run(app_graph, thread_id: str, payload):
    config = {"configurable": {"thread_id": thread_id}}
    return app_graph.invoke(payload, config=config)


def interrupt_of(result) -> dict:
    assert result.get("__interrupt__"), f"expected an interrupt, got {list(result)}"
    return result["__interrupt__"][0].value


# --------------------------------------------------------------------------


def test_full_round_trip(app_graph, fake_hermes, no_materials):
    state = run(app_graph, "t1", new_state(GOAL))

    # 1. clarify interrupt, with the chips the UI renders
    clarify = interrupt_of(state)
    assert clarify["kind"] == "clarify"
    assert clarify["questions"]
    assert clarify["suggested_answers"][0] == ["CHEM 2010, Oct 3", "Not sure yet"]

    # 2. answering it produces the draft roadmap and the approval interrupt
    state = run(app_graph, "t1", Command(resume={"answers": ["CHEM 2010, Oct 3"]}))
    approval = interrupt_of(state)
    assert approval["kind"] == "approval"
    milestones = approval["milestones"]
    assert len(milestones) == 3
    # Show-all: the whole list arrives at once, plus the clarify history.
    assert approval["clarification_turns"][0]["answer"] == "CHEM 2010, Oct 3"
    assert approval["clarified_goal"].startswith("Be ready for")
    # Nothing uploaded, so every milestone took the research branch.
    assert {m["source"] for m in milestones} == {"research"}
    assert all(m["reason"] for m in milestones)
    assert all(m["est_effort"] == "45–60 min" for m in milestones)

    # 3. reorder, then edit -- each returns to approval, never straight to commit
    reversed_ids = [m["id"] for m in reversed(milestones)]
    state = run(
        app_graph, "t1", Command(resume={"action": "reorder", "ids_in_order": reversed_ids})
    )
    after_reorder = interrupt_of(state)["milestones"]
    assert [m["id"] for m in after_reorder] == reversed_ids
    assert [m["order"] for m in after_reorder] == [1, 2, 3]

    state = run(
        app_graph,
        "t1",
        Command(
            resume={
                "action": "edit",
                "milestone_id": reversed_ids[0],
                "fields": {"title": "Entropy, properly"},
            }
        ),
    )
    edited = interrupt_of(state)["milestones"]
    assert edited[0]["title"] == "Entropy, properly"
    assert edited[0]["status"] == "edited"

    # 4. approve_all commits
    final = run(app_graph, "t1", Command(resume={"action": "approve_all"}))
    assert final["status"] == "committed"
    goal_id = final["goal_id"]
    assert goal_id

    with connection() as conn:
        goal = repo.get_goal(conn, goal_id)
        rows = repo.list_milestones(conn, goal_id)

    assert goal["title"] == "Be ready for the CHEM 2010 thermodynamics midterm on Oct 3"
    assert goal["course_code"] == "CHEM 2010"
    assert goal["short_name"] == "Thermo midterm"
    assert goal["due_at"] == "2026-10-03"
    assert goal["status"] == "committed"
    # Persisted in the order the student approved, not the order the model produced.
    assert [m["title"] for m in rows] == [m["title"] for m in edited]
    assert [m["order"] for m in rows] == [1, 2, 3]
    assert all(m["status"] == "approved" or m["status"] == "edited" for m in rows)


def test_reject_drops_the_milestone_and_renumbers(app_graph, fake_hermes, no_materials):
    run(app_graph, "t2", new_state(GOAL))
    state = run(app_graph, "t2", Command(resume="CHEM 2010, Oct 3"))
    milestones = interrupt_of(state)["milestones"]
    victim = milestones[1]["id"]

    state = run(app_graph, "t2", Command(resume={"action": "reject", "milestone_id": victim}))
    shown = interrupt_of(state)["milestones"]
    # Still shown (struck through) until commit, and the survivors renumber
    # contiguously so the visible numbering has no gap.
    assert [m["order"] for m in shown if m["status"] != "rejected"] == [1, 2]

    final = run(app_graph, "t2", Command(resume={"action": "approve_all"}))
    with connection() as conn:
        rows = repo.list_milestones(conn, final["goal_id"])
    assert len(rows) == 2
    assert victim not in [m["id"] for m in rows]


def test_add_milestone_is_user_sourced(app_graph, fake_hermes, no_materials):
    run(app_graph, "t3", new_state(GOAL))
    run(app_graph, "t3", Command(resume="CHEM 2010, Oct 3"))
    state = run(
        app_graph,
        "t3",
        Command(
            resume={
                "action": "add_milestone",
                "fields": {"title": "Office hours on cycles", "description": "Ask about Carnot."},
            }
        ),
    )
    added = [m for m in interrupt_of(state)["milestones"] if m["title"] == "Office hours on cycles"]
    assert len(added) == 1
    assert added[0]["source"] == "user"
    assert added[0]["related_topic_ids"] == []
    assert added[0]["reason"] == "Added by you."

    final = run(app_graph, "t3", Command(resume={"action": "approve_all"}))
    with connection() as conn:
        rows = repo.list_milestones(conn, final["goal_id"])
    user_rows = [m for m in rows if m["title"] == "Office hours on cycles"]
    assert user_rows and user_rows[0]["source"] == "user"


def test_clarify_turn_guard_stops_the_loop(app_graph, monkeypatch, no_materials):
    """A model that never stops asking must not produce an endless interrupt loop."""
    fake = FakeHermes(clarify_rounds=99)

    async def complete(prompt: str, *, system: str | None = None) -> str:
        return fake.complete(prompt, system=system)

    monkeypatch.setattr("agent.hermes.complete", complete)

    state = run(app_graph, "t4", new_state(GOAL))
    for _ in range(10):
        if not state.get("__interrupt__"):
            break
        if interrupt_of(state)["kind"] == "approval":
            break
        state = run(app_graph, "t4", Command(resume="still not sure"))

    assert interrupt_of(state)["kind"] == "approval"
    # MAX_CLARIFY_TURNS answered rounds, then it commits to its best reading. The
    # guard is checked before the call, so the fourth question is never even
    # generated -- three rounds, three Hermes calls.
    assert fake.clarify_calls == MAX_CLARIFY_TURNS
    assert len(interrupt_of(state)["clarification_turns"]) == MAX_CLARIFY_TURNS
    # The goal falls back to the student's own words rather than blocking.
    assert interrupt_of(state)["clarified_goal"].startswith(GOAL)


def test_materials_grounding_and_ordering(app_graph, fake_hermes, monkeypatch):
    """A milestone whose content is in Materials gets tagged `materials`, and a
    weak topic outranks a strong one on need alone (no calendar deadline --
    Step 5 isn't built, see `app.goals.context.calendar_context`)."""
    with connection() as conn:
        conn.execute(
            "INSERT INTO topics (id, name, user_understanding) VALUES (1, 'Entropy', 12)"
        )
        conn.execute(
            "INSERT INTO topics (id, name, user_understanding) VALUES (2, 'Fundamentals', 88)"
        )

    def search(topic, query, *, limit=5):
        if "entropy" in query.lower():
            return {
                "results": [
                    {"chunk_id": 11, "topic_id": 1, "topic_name": "Entropy", "distance": 0.4},
                    {"chunk_id": 12, "topic_id": 1, "topic_name": "Entropy", "distance": 0.5},
                ]
            }
        if "fundamental" in query.lower():
            return {
                "results": [
                    {"chunk_id": 21, "topic_id": 2, "topic_name": "Fundamentals", "distance": 0.6}
                ]
            }
        return {"results": []}

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", search)

    run(app_graph, "t5", new_state(GOAL))
    state = run(app_graph, "t5", Command(resume="CHEM 2010, Oct 3"))
    milestones = interrupt_of(state)["milestones"]

    by_title = {m["title"]: m for m in milestones}
    entropy = by_title["Rebuild entropy from the ground up"]
    fundamentals = by_title["Review thermodynamics fundamentals"]

    assert entropy["source"] == "materials"
    assert entropy["related_topic_ids"] == [1]
    assert entropy["source_chunk_ids"] == [11, 12]
    assert fundamentals["source"] == "materials"
    # Weak topic (12/100) outranks a strong one (88/100) on need alone, even
    # though the model put fundamentals first.
    assert entropy["order"] < fundamentals["order"]
    assert milestones[0]["title"] == "Rebuild entropy from the ground up"

    final = run(app_graph, "t5", Command(resume={"action": "approve_all"}))
    with connection() as conn:
        rows = repo.list_milestones(conn, final["goal_id"])
        goal = repo.get_goal(conn, final["goal_id"])
    # Provenance is stored, so "See related materials" resolves without a search.
    stored = {m["title"]: m for m in rows}
    assert stored["Rebuild entropy from the ground up"]["source_chunk_ids"] == [11, 12]
    assert goal["order_rationale"]


def test_provenance_is_what_personalize_saw_not_a_later_search(
    app_graph, fake_hermes, monkeypatch
):
    """The approval interrupt can sit for minutes. Re-searching at commit time
    would store chunks the student never approved."""
    with connection() as conn:
        conn.execute("INSERT INTO topics (id, name, user_understanding) VALUES (1, 'Entropy', 30)")

    calls = {"n": 0}

    def drifting_search(topic, query, *, limit=5):
        calls["n"] += 1
        chunk = 100 + calls["n"]
        return {
            "results": [
                {"chunk_id": chunk, "topic_id": 1, "topic_name": "Entropy", "distance": 0.3}
            ]
        }

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", drifting_search)

    run(app_graph, "t6", new_state(GOAL))
    state = run(app_graph, "t6", Command(resume="CHEM 2010, Oct 3"))
    approved = {m["id"]: m["source_chunk_ids"] for m in interrupt_of(state)["milestones"]}
    searches_during_personalize = calls["n"]

    final = run(app_graph, "t6", Command(resume={"action": "approve_all"}))
    with connection() as conn:
        rows = repo.list_milestones(conn, final["goal_id"])

    assert calls["n"] == searches_during_personalize, "commit must not re-search"
    for row in rows:
        assert row["source_chunk_ids"] == approved[row["id"]]
