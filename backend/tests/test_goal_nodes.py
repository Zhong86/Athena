"""Per-node behaviour, called directly rather than through the graph.

The graph test covers the happy path end to end; these cover the branches it
cannot reach cheaply -- a dead gateway, a model that returns junk, and the
deterministic edit rules.
"""

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "nodes.db")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402

from agent import hermes  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.goals.llm import LLMUnavailable  # noqa: E402
from app.goals.nodes.approve import apply_edits  # noqa: E402
from app.goals.nodes.clarify import clarify_intent  # noqa: E402
from app.goals.nodes.decompose import decompose_goal  # noqa: E402
from app.goals.nodes.personalize import personalize_decomposition  # noqa: E402
from app.goals.state import MAX_MILESTONES, Milestone, new_state  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM topics")
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


def milestone(**overrides) -> Milestone:
    base: Milestone = {
        "id": "m1",
        "title": "Rebuild entropy",
        "description": "Derivations.",
        "order": 1,
        "status": "proposed",
        "reason": "",
        "source": "research",
        "related_topic_ids": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# --------------------------------------------------------------------------
# clarify_intent
# --------------------------------------------------------------------------


def test_clarify_parks_questions_rather_than_interrupting_immediately(hermes_says):
    """The interrupt happens on the *next* pass. That ordering is what keeps a
    resumed node from re-asking Hermes before it records the answer."""
    hermes_says(
        {
            "needs_clarification": True,
            "questions": ["Which course?", "When is it?"],
            "suggested_answers": [["CHEM 2010"], ["Oct 3"]],
            "extracted": {},
        }
    )
    result = clarify_intent(new_state("pass the midterm"))
    assert result["clarifying_questions"] == ["Which course?", "When is it?"]
    assert result["suggested_answers"] == [["CHEM 2010"], ["Oct 3"]]
    assert result["status"] == "clarifying"
    assert "clarified_goal" not in result


def test_clarify_caps_questions_at_two(hermes_says):
    hermes_says(
        {
            "needs_clarification": True,
            "questions": ["a?", "b?", "c?", "d?"],
            "extracted": {},
        }
    )
    assert len(clarify_intent(new_state("x"))["clarifying_questions"]) == 2


def test_clarify_proceeds_on_the_students_own_words_when_hermes_is_down(hermes_dead):
    """Clarification is a nicety; blocking the run over it is not acceptable."""
    state = new_state("pass the thermo midterm")
    result = clarify_intent(state)
    assert result["clarified_goal"] == "pass the thermo midterm"
    assert result["status"] == "decomposing"


def test_clarify_folds_answers_into_the_fallback_goal(hermes_dead):
    state = new_state("pass the midterm")
    state["clarification_turns"] = [{"question": "Which course?", "answer": "CHEM 2010"}]
    assert clarify_intent(state)["clarified_goal"] == "pass the midterm CHEM 2010"


def test_clarify_extracts_the_goal_fields_commit_needs(hermes_says):
    hermes_says(
        {
            "needs_clarification": False,
            "clarified_goal": "Be ready for the CHEM 2010 midterm on Oct 3",
            "extracted": {"course_code": "CHEM 2010", "due_at": "2026-10-03"},
        }
    )
    result = clarify_intent(new_state("pass the midterm"))
    assert result["goal_fields"]["course_code"] == "CHEM 2010"
    assert result["clarifying_questions"] == []


# --------------------------------------------------------------------------
# decompose_goal
# --------------------------------------------------------------------------


def test_decompose_assigns_stable_sequential_ids(hermes_says):
    """Ids are assigned once, here. A replay that regenerated them would orphan
    every reorder and reject the student had already made."""
    hermes_says({"milestones": [{"title": "One"}, {"title": "Two"}, {"title": "Three"}]})
    state = new_state("goal")
    state["clarified_goal"] = "a clear goal"
    drafts = decompose_goal(state)["draft_milestones"]
    assert [m["id"] for m in drafts] == ["m1", "m2", "m3"]
    assert [m["order"] for m in drafts] == [1, 2, 3]
    assert decompose_goal(state)["draft_milestones"][0]["id"] == "m1"


def test_decompose_skips_untitled_items_without_leaving_id_gaps(hermes_says):
    hermes_says({"milestones": [{"title": "One"}, {"title": "  "}, {"title": "Two"}]})
    drafts = decompose_goal(new_state("goal"))["draft_milestones"]
    assert [m["id"] for m in drafts] == ["m1", "m2"]
    assert [m["title"] for m in drafts] == ["One", "Two"]


def test_decompose_caps_the_list(hermes_says):
    hermes_says({"milestones": [{"title": f"Step {i}"} for i in range(20)]})
    assert len(decompose_goal(new_state("goal"))["draft_milestones"]) == MAX_MILESTONES


def test_decompose_raises_when_hermes_is_down(hermes_dead):
    """Unlike clarification, this cannot degrade: a roadmap is the product, and
    an empty goal must never be committed."""
    with pytest.raises(LLMUnavailable):
        decompose_goal(new_state("goal"))


def test_decompose_raises_on_an_empty_milestone_list(hermes_says):
    hermes_says({"milestones": []})
    with pytest.raises(LLMUnavailable):
        decompose_goal(new_state("goal"))


def test_unparseable_json_is_llm_unavailable_not_a_crash(hermes_says):
    hermes_says("I'd be happy to help with that!")
    with pytest.raises(LLMUnavailable):
        decompose_goal(new_state("goal"))


def test_json_wrapped_in_prose_is_salvaged(hermes_says):
    hermes_says('Sure! {"milestones": [{"title": "One"}]} Hope that helps.')
    assert len(decompose_goal(new_state("goal"))["draft_milestones"]) == 1


# --------------------------------------------------------------------------
# personalize_decomposition
# --------------------------------------------------------------------------


def _state_with_drafts(drafts, topics=(), deadlines=()):
    state = new_state("goal")
    state["clarified_goal"] = "a clear goal"
    state["draft_milestones"] = drafts
    state["materials_context"] = {"topics": list(topics)}
    state["calendar_context"] = {"deadlines": list(deadlines)}
    return state


def test_grounded_milestone_is_tagged_materials(monkeypatch, hermes_says):
    hermes_says({"reason": "short", "reason_long": "long", "est_effort_min": 30, "est_effort_max": 45})
    monkeypatch.setattr(
        "app.goals.nodes.personalize.search_materials",
        lambda topic, query, *, limit=5: {
            "results": [{"chunk_id": 5, "topic_id": 1, "topic_name": "Entropy", "distance": 0.3}]
        },
    )
    state = _state_with_drafts(
        [milestone()], topics=[{"id": 1, "name": "Entropy", "user_understanding": 20}]
    )
    result = personalize_decomposition(state)
    assert result["decomposition_source"] == "materials"
    out = result["draft_milestones"][0]
    assert out["source"] == "materials"
    assert out["related_topic_ids"] == [1]
    assert out["source_chunk_ids"] == [5]
    assert out["est_effort"] == "30–45 min"


def test_ungrounded_milestone_takes_the_research_branch(monkeypatch, hermes_says):
    """The spec's branch. Forcing a materials link here would be invisible
    afterwards -- nothing on screen distinguishes a real link from a forced one."""
    hermes_says({"reason": "short", "reason_long": "long"})
    monkeypatch.setattr(
        "app.goals.nodes.personalize.search_materials",
        lambda topic, query, *, limit=5: {"results": []},
    )
    result = personalize_decomposition(_state_with_drafts([milestone()]))
    assert result["decomposition_source"] == "research"
    assert result["draft_milestones"][0]["source"] == "research"
    assert result["draft_milestones"][0]["related_topic_ids"] == []


def test_a_distant_hit_does_not_count_as_grounding(monkeypatch, hermes_says):
    """In a small corpus the nearest chunk is always *something*; grounding on it
    would put a topic name in the copy that has nothing to do with the work."""
    hermes_says({"reason": "short", "reason_long": "long"})
    monkeypatch.setattr(
        "app.goals.nodes.personalize.search_materials",
        lambda topic, query, *, limit=5: {
            "results": [{"chunk_id": 5, "topic_id": 1, "topic_name": "Titration", "distance": 9.9}]
        },
    )
    result = personalize_decomposition(_state_with_drafts([milestone()]))
    assert result["draft_milestones"][0]["source"] == "research"


def test_mixed_sources_are_reported_as_mixed(monkeypatch, hermes_says):
    hermes_says({"reason": "short", "reason_long": "long"})

    def search(topic, query, *, limit=5):
        if "entropy" in query.lower():
            return {"results": [{"chunk_id": 5, "topic_id": 1, "topic_name": "Entropy", "distance": 0.2}]}
        return {"results": []}

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", search)
    state = _state_with_drafts(
        [milestone(id="m1", title="Rebuild entropy"), milestone(id="m2", title="Lab safety")],
        topics=[{"id": 1, "name": "Entropy", "user_understanding": 20}],
    )
    assert personalize_decomposition(state)["decomposition_source"] == "mixed"


def test_copy_falls_back_to_the_rankers_own_sentence(monkeypatch, hermes_dead):
    """Less fluent, but true. The reason matters more than the prose."""
    monkeypatch.setattr(
        "app.goals.nodes.personalize.search_materials",
        lambda topic, query, *, limit=5: {
            "results": [{"chunk_id": 5, "topic_id": 1, "topic_name": "Entropy", "distance": 0.2}]
        },
    )
    state = _state_with_drafts(
        [milestone()], topics=[{"id": 1, "name": "Entropy", "user_understanding": 18}]
    )
    out = personalize_decomposition(state)["draft_milestones"][0]
    assert "Entropy is scoring weak (18/100)" in out["reason"]


def test_a_search_failure_is_not_fatal(monkeypatch, hermes_says):
    """An unwarmed embedding model or missing Lance table degrades to research,
    it does not take the run down."""
    hermes_says({"reason": "short", "reason_long": "long"})

    def boom(topic, query, *, limit=5):
        raise RuntimeError("lance table missing")

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", boom)
    result = personalize_decomposition(_state_with_drafts([milestone()]))
    assert result["draft_milestones"][0]["source"] == "research"


def test_unlocks_after_is_only_set_between_topic_siblings(monkeypatch, hermes_says):
    """Prerequisites come from shared topics, not from the model: a model asked
    to invent dependencies invents cycles."""
    hermes_says({"reason": "short", "reason_long": "long"})

    def search(topic, query, *, limit=5):
        topic_id = 1 if "entropy" in query.lower() else 2
        return {
            "results": [
                {"chunk_id": topic_id, "topic_id": topic_id, "topic_name": "T", "distance": 0.2}
            ]
        }

    monkeypatch.setattr("app.goals.nodes.personalize.search_materials", search)
    state = _state_with_drafts(
        [
            milestone(id="m1", title="Entropy basics"),
            milestone(id="m2", title="Entropy problem sets"),
            milestone(id="m3", title="Lab safety"),
        ],
        topics=[
            {"id": 1, "name": "Entropy", "user_understanding": 20},
            {"id": 2, "name": "Safety", "user_understanding": 20},
        ],
    )
    out = {m["id"]: m for m in personalize_decomposition(state)["draft_milestones"]}
    assert out["m2"].get("unlocks_after") == "m1"
    assert "unlocks_after" not in out["m1"]
    # No cycles, ever.
    assert all(m.get("unlocks_after") != m["id"] for m in out.values())


# --------------------------------------------------------------------------
# apply_edits -- deterministic, no LLM
# --------------------------------------------------------------------------


def _approval_state(milestones, action):
    state = new_state("goal")
    state["milestones"] = milestones
    state["pending_action"] = action
    return state


def test_reject_renumbers_contiguously():
    milestones = [milestone(id=f"m{i}", order=i) for i in (1, 2, 3)]
    result = apply_edits(_approval_state(milestones, {"action": "reject", "milestone_id": "m2"}))
    survivors = [m for m in result["milestones"] if m["status"] != "rejected"]
    assert [m["order"] for m in survivors] == [1, 2]
    assert result["approval_complete"] is False


def test_reject_clears_a_pointer_at_the_rejected_milestone():
    milestones = [
        milestone(id="m1", order=1),
        milestone(id="m2", order=2, unlocks_after="m1"),
    ]
    result = apply_edits(_approval_state(milestones, {"action": "reject", "milestone_id": "m1"}))
    assert "unlocks_after" not in result["milestones"][1]


def test_reorder_keeps_milestones_the_payload_omitted():
    milestones = [milestone(id=f"m{i}", order=i) for i in (1, 2, 3)]
    result = apply_edits(_approval_state(milestones, {"action": "reorder", "ids_in_order": ["m3"]}))
    assert [m["id"] for m in result["milestones"]] == ["m3", "m1", "m2"]
    assert [m["order"] for m in result["milestones"]] == [1, 2, 3]


def test_edit_marks_the_milestone_edited():
    result = apply_edits(
        _approval_state(
            [milestone(id="m1")],
            {"action": "edit", "milestone_id": "m1", "fields": {"title": "New title"}},
        )
    )
    assert result["milestones"][0]["title"] == "New title"
    assert result["milestones"][0]["status"] == "edited"


def test_edit_ignores_fields_that_are_not_the_students_to_set():
    """`source` and `related_topic_ids` are provenance, not copy."""
    result = apply_edits(
        _approval_state(
            [milestone(id="m1", source="research")],
            {"action": "edit", "milestone_id": "m1", "fields": {"source": "materials"}},
        )
    )
    assert result["milestones"][0]["source"] == "research"


def test_unknown_action_is_ignored_not_fatal():
    result = apply_edits(_approval_state([milestone(id="m1")], {"action": "explode"}))
    assert len(result["milestones"]) == 1
    assert result["approval_complete"] is False


def test_add_milestone_with_nothing_attached_is_user_sourced():
    result = apply_edits(
        _approval_state(
            [milestone(id="m1", order=1)],
            {"action": "add_milestone", "fields": {"title": "Office hours"}},
        )
    )
    added = result["milestones"][-1]
    assert added["source"] == "user"
    assert added["related_topic_ids"] == []
    assert added["reason"] == "Added by you."
    assert added["order"] == 2


def test_add_milestone_with_existing_topics_is_a_materials_row():
    with connection() as conn:
        conn.execute("INSERT INTO topics (id, name) VALUES (3, 'Cycles')")
    result = apply_edits(
        _approval_state(
            [milestone(id="m1", order=1)],
            {
                "action": "add_milestone",
                "fields": {"title": "Carnot cycles", "related_topic_ids": [3]},
            },
        )
    )
    added = result["milestones"][-1]
    assert added["source"] == "materials"
    assert added["related_topic_ids"] == [3]


def test_add_milestone_can_name_a_new_topic():
    """Created with auto_created = 0: Materials distinguishes topics Hermes
    invented from topics the student named."""
    result = apply_edits(
        _approval_state(
            [milestone(id="m1", order=1)],
            {
                "action": "add_milestone",
                "fields": {"title": "Reaction kinetics drills", "new_topic_name": "Kinetics"},
            },
        )
    )
    added = result["milestones"][-1]
    with connection() as conn:
        row = conn.execute("SELECT * FROM topics WHERE name = 'Kinetics'").fetchone()
    assert row["auto_created"] == 0
    assert added["related_topic_ids"] == [row["id"]]
    assert added["source"] == "materials"


def test_naming_an_existing_topic_reuses_it():
    with connection() as conn:
        conn.execute("INSERT INTO topics (id, name) VALUES (4, 'Entropy')")
    apply_edits(
        _approval_state(
            [milestone(id="m1", order=1)],
            {"action": "add_milestone", "fields": {"title": "More entropy", "new_topic_name": "entropy"}},
        )
    )
    with connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM topics WHERE name LIKE 'entropy'").fetchone()
    assert count["n"] == 1


def test_add_milestone_respects_the_position():
    milestones = [milestone(id="m1", order=1), milestone(id="m2", order=2)]
    result = apply_edits(
        _approval_state(
            milestones,
            {"action": "add_milestone", "fields": {"title": "First thing", "position": 0}},
        )
    )
    assert [m["title"] for m in result["milestones"]][0] == "First thing"
    assert [m["order"] for m in result["milestones"]] == [1, 2, 3]


def test_add_milestone_never_reuses_a_rejected_id():
    """A rejected m2 and a newly added milestone sharing an id would make the
    next reorder ambiguous."""
    milestones = [
        milestone(id="m1", order=1),
        milestone(id="m2", order=2, status="rejected"),
    ]
    result = apply_edits(
        _approval_state(milestones, {"action": "add_milestone", "fields": {"title": "New"}})
    )
    ids = [m["id"] for m in result["milestones"]]
    assert len(set(ids)) == len(ids)
    assert result["milestones"][-1]["id"] == "m3"


def test_add_milestone_stops_at_the_cap():
    milestones = [milestone(id=f"m{i}", order=i) for i in range(1, MAX_MILESTONES + 1)]
    result = apply_edits(
        _approval_state(milestones, {"action": "add_milestone", "fields": {"title": "One more"}})
    )
    assert len(result["milestones"]) == MAX_MILESTONES


def test_add_milestone_without_a_title_is_ignored():
    result = apply_edits(
        _approval_state([milestone(id="m1")], {"action": "add_milestone", "fields": {}})
    )
    assert len(result["milestones"]) == 1
