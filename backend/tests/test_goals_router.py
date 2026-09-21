"""The HTTP surface: the envelope, the graph endpoints, and the post-commit CRUD
that "Adjust roadmap" drives.

Hermes is stubbed. The checkpointer is the real SqliteSaver, pointed at a temp
directory -- resuming across requests is the behaviour under test, and an
in-memory saver would not exercise it.
"""

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "router.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402

GOAL = "Want to pass the Thermo midterm"


def _hermes_reply(prompt: str) -> str:
    if "Decide whether this goal is specific enough" in prompt:
        return json.dumps(
            {
                "needs_clarification": False,
                "questions": [],
                "clarified_goal": "Be ready for the CHEM 2010 thermo midterm",
                "extracted": {
                    "short_name": "Thermo midterm",
                    "course_code": "CHEM 2010",
                    "category": "academic",
                    "due_at": "2026-10-03",
                },
            }
        )
    if "Break this into" in prompt:
        return json.dumps(
            {
                "milestones": [
                    {"title": "Review fundamentals", "description": "Refresher."},
                    {"title": "Rebuild entropy", "description": "Derivations."},
                    {"title": "Second-law problem sets", "description": "Practice."},
                ]
            }
        )
    if "Write the two explanations" in prompt:
        title = re.search(r'Milestone: "(.*?)"', prompt).group(1)
        return json.dumps(
            {
                "reason": f"short reason for {title}",
                "reason_long": f"long reason for {title}",
                "est_effort_min": 30,
                "est_effort_max": 45,
            }
        )
    raise AssertionError(f"unexpected prompt: {prompt[:120]}")


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    async def complete(prompt: str, *, system: str | None = None) -> str:
        return _hermes_reply(prompt)

    monkeypatch.setattr("agent.hermes.complete", complete)
    # No materials: grounding is exercised in test_goal_graph.py, and a real
    # vector search here would need a warmed embedding model.
    monkeypatch.setattr(
        "app.goals.nodes.personalize.search_materials",
        lambda topic, query, *, limit=5: {"results": []},
    )
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM roadmap_runs")
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def commit_a_goal(client) -> tuple[str, int]:
    """Start -> approve_all, the shortest path to a committed goal."""
    started = client.post("/goals/roadmap", json={"raw_goal_input": GOAL}).json()
    thread_id = started["thread_id"]
    assert started["interrupt"]["kind"] == "approval"
    done = client.post(
        f"/goals/roadmap/{thread_id}/resume",
        json={"payload": {"action": "approve_all"}},
    ).json()
    assert done["goal_id"]
    return thread_id, done["goal_id"]


# --------------------------------------------------------------------------
# the envelope
# --------------------------------------------------------------------------


def test_start_returns_the_envelope(client):
    body = client.post("/goals/roadmap", json={"raw_goal_input": GOAL}).json()
    assert set(body) == {"thread_id", "status", "interrupt", "goal_id", "raw_goal_input"}
    assert body["status"] == "awaiting_approval"
    # Echoed back because the creation flow redraws the student's opening line
    # after a reload, and no interrupt payload carries it.
    assert body["raw_goal_input"] == GOAL
    assert body["goal_id"] is None
    assert len(body["interrupt"]["milestones"]) == 3


def test_committed_run_has_no_interrupt_and_a_goal_id(client):
    _, goal_id = commit_a_goal(client)
    assert isinstance(goal_id, int)


def test_run_state_survives_a_reload(client):
    """"Save and exit" comes back to this: a GET has to rebuild the interrupt
    the page was showing, without advancing the graph."""
    started = client.post("/goals/roadmap", json={"raw_goal_input": GOAL}).json()
    reloaded = client.get(f"/goals/roadmap/{started['thread_id']}").json()
    assert reloaded["status"] == "awaiting_approval"
    assert reloaded["interrupt"]["kind"] == "approval"
    assert reloaded["interrupt"]["milestones"] == started["interrupt"]["milestones"]


def test_resume_with_unknown_thread_is_404(client):
    response = client.post(
        "/goals/roadmap/roadmap-nope/resume", json={"payload": {"action": "approve_all"}}
    )
    assert response.status_code == 404


def test_resume_after_commit_is_409_not_a_second_goal(client):
    thread_id, goal_id = commit_a_goal(client)
    response = client.post(
        f"/goals/roadmap/{thread_id}/resume", json={"payload": {"action": "approve_all"}}
    )
    assert response.status_code == 409
    assert len(client.get("/goals").json()) == 1


def test_abandoned_run_cannot_be_resumed(client):
    started = client.post("/goals/roadmap", json={"raw_goal_input": GOAL}).json()
    assert client.delete(f"/goals/roadmap/{started['thread_id']}").status_code == 204
    response = client.post(
        f"/goals/roadmap/{started['thread_id']}/resume",
        json={"payload": {"action": "approve_all"}},
    )
    assert response.status_code == 409
    # And it never reaches the goal list.
    assert client.get("/goals").json() == []


def test_hermes_down_is_a_503_not_an_empty_goal(client, monkeypatch):
    from agent import hermes

    async def dead(prompt: str, *, system: str | None = None) -> str:
        raise hermes.HermesError("connection refused")

    monkeypatch.setattr("agent.hermes.complete", dead)
    response = client.post("/goals/roadmap", json={"raw_goal_input": GOAL})
    assert response.status_code == 503
    assert client.get("/goals").json() == []


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def test_detail_returns_full_accordion_detail_for_every_milestone(client):
    """Not a trimmed list: every row on the detail page expands, and the focus
    milestone renders expanded on arrival."""
    _, goal_id = commit_a_goal(client)
    detail = client.get(f"/goals/{goal_id}").json()

    assert detail["title"] == "Be ready for the CHEM 2010 thermo midterm"
    assert detail["course_code"] == "CHEM 2010"
    assert detail["percent"] == 0
    assert detail["total_count"] == 3

    for milestone in detail["milestones"]:
        assert milestone["reason"]
        assert milestone["reason_long"]
        assert milestone["est_effort"] == "30–45 min"
        assert "source_chunks" in milestone
    # The first milestone is the focus item the page expands by default.
    assert detail["milestones"][0]["progress_status"] == "current"
    assert [m["progress_status"] for m in detail["milestones"][1:]] == ["upcoming"] * 2


def test_goal_list_card_fields(client):
    _, goal_id = commit_a_goal(client)
    (card,) = client.get("/goals").json()
    assert card["id"] == goal_id
    assert card["short_name"] == "Thermo midterm"
    assert card["percent"] == 0
    assert card["focus_title"] == "Review fundamentals"


def test_unknown_goal_is_404(client):
    assert client.get("/goals/4242").status_code == 404


# --------------------------------------------------------------------------
# post-commit CRUD ("Adjust roadmap")
# --------------------------------------------------------------------------


def test_progress_percent_is_computed_from_milestones(client):
    _, goal_id = commit_a_goal(client)
    first = client.get(f"/goals/{goal_id}").json()["milestones"][0]["id"]
    client.patch(
        f"/goals/{goal_id}/milestones/{first}", json={"progress_status": "done"}
    )
    detail = client.get(f"/goals/{goal_id}").json()
    assert (detail["done_count"], detail["total_count"]) == (1, 3)
    assert detail["percent"] == 33


def test_marking_the_focus_done_promotes_the_next(client):
    """Otherwise the detail page loses its "Focus now" row and its actions with
    it -- `delete_milestone` already promotes a successor, and a progress change
    has to hold the same invariant."""
    _, goal_id = commit_a_goal(client)
    milestones = client.get(f"/goals/{goal_id}").json()["milestones"]
    client.patch(
        f"/goals/{goal_id}/milestones/{milestones[0]['id']}",
        json={"progress_status": "done"},
    )
    after = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert [m["progress_status"] for m in after] == ["done", "current", "upcoming"]


def test_only_one_milestone_is_ever_the_focus(client):
    """Two "Focus now" pills is the visible failure; promoting one has to demote
    whichever milestone held it."""
    _, goal_id = commit_a_goal(client)
    milestones = client.get(f"/goals/{goal_id}").json()["milestones"]
    body = client.patch(
        f"/goals/{goal_id}/milestones/{milestones[2]['id']}",
        json={"progress_status": "current"},
    ).json()
    assert body["progress_status"] == "current"

    after = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert [m["progress_status"] for m in after] == ["upcoming", "upcoming", "current"]


def test_a_finished_goal_has_no_focus_to_promote(client):
    """Every milestone done is a terminal state, not a state to promote out of."""
    _, goal_id = commit_a_goal(client)
    for milestone in client.get(f"/goals/{goal_id}").json()["milestones"]:
        client.patch(
            f"/goals/{goal_id}/milestones/{milestone['id']}",
            json={"progress_status": "done"},
        )
    detail = client.get(f"/goals/{goal_id}").json()
    assert detail["percent"] == 100
    assert [m["progress_status"] for m in detail["milestones"]] == ["done"] * 3


def test_unfinished_runs_are_listed_so_a_draft_can_be_reopened(client):
    """"Save and exit" would otherwise be a trapdoor: the thread id only ever
    lived in that page's URL."""
    started = client.post("/goals/roadmap", json={"raw_goal_input": GOAL}).json()
    listed = client.get("/goals/roadmap").json()
    assert [run["thread_id"] for run in listed] == [started["thread_id"]]
    assert listed[0]["raw_goal_input"] == GOAL

    # Committing takes it off the list -- it is a goal now, not a draft.
    client.post(
        f"/goals/roadmap/{started['thread_id']}/resume",
        json={"payload": {"action": "approve_all"}},
    )
    assert client.get("/goals/roadmap").json() == []


def test_reorder_leaves_no_duplicate_positions(client):
    _, goal_id = commit_a_goal(client)
    ids = [m["id"] for m in client.get(f"/goals/{goal_id}").json()["milestones"]]
    body = client.put(
        f"/goals/{goal_id}/milestones/order", json={"ids_in_order": list(reversed(ids))}
    ).json()
    assert [m["id"] for m in body] == list(reversed(ids))
    assert [m["order"] for m in body] == [1, 2, 3]


def test_partial_reorder_is_rejected(client):
    """Renumbering 2 of 3 would leave two milestones sharing an order_index."""
    _, goal_id = commit_a_goal(client)
    ids = [m["id"] for m in client.get(f"/goals/{goal_id}").json()["milestones"]]
    response = client.put(
        f"/goals/{goal_id}/milestones/order", json={"ids_in_order": ids[:2]}
    )
    assert response.status_code == 422
    unchanged = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert [m["id"] for m in unchanged] == ids


def test_deleting_the_focus_milestone_promotes_the_next(client):
    _, goal_id = commit_a_goal(client)
    milestones = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert client.delete(f"/goals/{goal_id}/milestones/{milestones[0]['id']}").status_code == 204

    after = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert len(after) == 2
    assert after[0]["id"] == milestones[1]["id"]
    assert after[0]["progress_status"] == "current"
    assert [m["order"] for m in after] == [1, 2]


def test_post_commit_add_is_user_sourced_and_shifts_the_rest(client):
    _, goal_id = commit_a_goal(client)
    created = client.post(
        f"/goals/{goal_id}/milestones",
        json={"title": "Office hours", "description": "Ask about cycles", "position": 1},
    )
    assert created.status_code == 201
    assert created.json()["source"] == "user"

    milestones = client.get(f"/goals/{goal_id}").json()["milestones"]
    assert milestones[0]["title"] == "Office hours"
    assert [m["order"] for m in milestones] == [1, 2, 3, 4]


def test_post_commit_add_with_topics_is_a_materials_row(client):
    _, goal_id = commit_a_goal(client)
    with connection() as conn:
        conn.execute("INSERT INTO topics (id, name, user_understanding) VALUES (9, 'Cycles', 40)")
    created = client.post(
        f"/goals/{goal_id}/milestones",
        json={"title": "Carnot cycles", "related_topic_ids": [9]},
    ).json()
    assert created["source"] == "materials"
    assert created["related_topic_ids"] == [9]
    # And the topic now shows up in the detail page's strength chips.
    strengths = client.get(f"/goals/{goal_id}").json()["topic_strengths"]
    assert [(s["name"], s["strength"]) for s in strengths] == [("Cycles", "fair")]


def test_goal_can_be_archived(client):
    _, goal_id = commit_a_goal(client)
    body = client.patch(f"/goals/{goal_id}", json={"status": "archived"}).json()
    assert body["status"] == "archived"
    assert client.get("/goals").json()[0]["status"] == "archived"


def test_paused_is_not_a_supported_status(client):
    """goal-list.html styles a Paused pill, but the schema's CHECK allows three
    values and widening it would mean rebuilding `goals`. Rejected at the edge
    rather than failing deep in SQL."""
    _, goal_id = commit_a_goal(client)
    assert client.patch(f"/goals/{goal_id}", json={"status": "paused"}).status_code == 422


def test_unknown_status_is_rejected(client):
    _, goal_id = commit_a_goal(client)
    assert client.patch(f"/goals/{goal_id}", json={"status": "elsewhere"}).status_code == 422


def test_editing_a_milestone_touches_the_goal(client):
    """The detail page's "Last updated 2 minutes ago" has to move."""
    _, goal_id = commit_a_goal(client)
    before = client.get(f"/goals/{goal_id}").json()
    first = before["milestones"][0]["id"]
    with connection() as conn:
        conn.execute("UPDATE goals SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (goal_id,))

    client.patch(f"/goals/{goal_id}/milestones/{first}", json={"title": "Renamed"})
    after = client.get(f"/goals/{goal_id}").json()
    assert after["updated_at"] != "2020-01-01T00:00:00Z"
    assert after["milestones"][0]["title"] == "Renamed"
