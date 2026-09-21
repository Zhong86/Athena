"""Tagger tests with Hermes stubbed -- no gateway, no network."""

import json

import pytest

from agent import hermes
from app.materials.ingest import tagger
from app.materials.ingest.tagger import MAX_NEW_TOPICS, assign_topics

pytestmark = pytest.mark.anyio

EXISTING = [
    {"id": 1, "name": "Entropy", "description": "disorder"},
    {"id": 2, "name": "Heat transfer", "description": None},
]


@pytest.fixture
def hermes_replies(monkeypatch):
    """Queue up one reply per expected Hermes call; records the prompts sent."""
    state = {"replies": [], "prompts": []}

    async def fake_complete(prompt, *, system=None):
        state["prompts"].append(prompt)
        if not state["replies"]:
            raise AssertionError("more Hermes calls than queued replies")
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(hermes, "complete", fake_complete)
    return state


def _reply(*assignments) -> str:
    return json.dumps({"assignments": list(assignments)})


async def test_assigns_to_existing_topic(hermes_replies):
    hermes_replies["replies"] = [_reply({"index": 0, "topic_id": 2})]
    result = await assign_topics(["heat flows"], EXISTING)
    assert result.refs == [2]
    assert result.proposed == []


async def test_creates_new_topic_with_placeholder_ref(hermes_replies):
    hermes_replies["replies"] = [
        _reply({"index": 0, "topic_name": "Gibbs free energy", "topic_description": "G"})
    ]
    result = await assign_topics(["dG = dH - TdS"], EXISTING)
    assert result.refs == [-1]
    assert [(p.ref, p.name) for p in result.proposed] == [(-1, "Gibbs free energy")]


async def test_existing_topic_matched_by_name_case_insensitively(hermes_replies):
    """The model routinely answers with a name instead of the id it was given."""
    hermes_replies["replies"] = [_reply({"index": 0, "topic_name": "entropy"})]
    result = await assign_topics(["disorder rises"], EXISTING)
    assert result.refs == [1]
    assert result.proposed == []


async def test_same_new_topic_across_batches_is_created_once(hermes_replies):
    """The cross-batch dedupe: batch 2 must reuse batch 1's proposal rather
    than inventing a second copy of the same name."""
    texts = [f"chunk {i}" for i in range(tagger.BATCH_SIZE + 1)]
    hermes_replies["replies"] = [
        _reply(*[{"index": i, "topic_name": "Carnot cycle"} for i in range(tagger.BATCH_SIZE)]),
        _reply({"index": 0, "topic_name": "carnot cycle"}),
    ]
    result = await assign_topics(texts, EXISTING)

    assert len(result.proposed) == 1
    assert set(result.refs) == {-1}
    # Batch 2's prompt must actually contain the batch-1 proposal, otherwise
    # the dedupe is luck rather than design.
    assert "Carnot cycle" in hermes_replies["prompts"][1]


async def test_new_topic_cap_folds_extras_into_nearest_existing(hermes_replies):
    names = [f"Topic number {i}" for i in range(MAX_NEW_TOPICS)]
    hermes_replies["replies"] = [
        _reply(
            *[{"index": i, "topic_name": n} for i, n in enumerate(names)],
            # One over the cap, but close enough to an existing name to fold in.
            {"index": MAX_NEW_TOPICS, "topic_name": "Entropyy"},
        )
    ]
    result = await assign_topics([f"c{i}" for i in range(MAX_NEW_TOPICS + 1)], EXISTING)

    assert len(result.proposed) == MAX_NEW_TOPICS
    assert result.refs[-1] == 1  # folded into the existing "Entropy"


async def test_over_cap_with_no_similar_topic_is_unassigned(hermes_replies):
    names = [f"Topic number {i}" for i in range(MAX_NEW_TOPICS)]
    hermes_replies["replies"] = [
        _reply(
            *[{"index": i, "topic_name": n} for i, n in enumerate(names)],
            {"index": MAX_NEW_TOPICS, "topic_name": "Quantum chromodynamics"},
        )
    ]
    result = await assign_topics([f"c{i}" for i in range(MAX_NEW_TOPICS + 1)], EXISTING)
    assert result.refs[-1] is None


async def test_json_wrapped_in_fences_and_prose_still_parses(hermes_replies):
    hermes_replies["replies"] = [
        "Sure, here you go:\n```json\n" + _reply({"index": 0, "topic_id": 1}) + "\n```\nHope that helps!"
    ]
    result = await assign_topics(["x"], EXISTING)
    assert result.refs == [1]


async def test_malformed_json_leaves_chunks_unassigned(hermes_replies):
    hermes_replies["replies"] = ["I'm afraid I can't do that."]
    result = await assign_topics(["x", "y"], EXISTING)
    assert result.refs == [None, None]


async def test_hallucinated_topic_id_is_unassigned(hermes_replies):
    hermes_replies["replies"] = [_reply({"index": 0, "topic_id": 999})]
    result = await assign_topics(["x"], EXISTING)
    assert result.refs == [None]


async def test_missing_assignment_leaves_that_chunk_unassigned(hermes_replies):
    """A short reply must not shift every later chunk onto the wrong topic."""
    hermes_replies["replies"] = [_reply({"index": 0, "topic_id": 1}, {"index": 2, "topic_id": 2})]
    result = await assign_topics(["a", "b", "c"], EXISTING)
    assert result.refs == [1, None, 2]


async def test_hermes_down_does_not_lose_the_upload(hermes_replies):
    hermes_replies["replies"] = [hermes.HermesError("connection refused")]
    result = await assign_topics(["a", "b"], EXISTING)
    assert result.refs == [None, None]


async def test_batches_cover_every_chunk(hermes_replies):
    total = tagger.BATCH_SIZE * 2 + 3
    hermes_replies["replies"] = [_reply(), _reply(), _reply()]
    result = await assign_topics([f"c{i}" for i in range(total)], EXISTING)
    assert len(result.refs) == total
    assert len(hermes_replies["prompts"]) == 3
