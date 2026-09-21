"""The row<->state boundary, against a really-migrated SQLite.

Every mapping in here is a place where the graph's vocabulary and the schema's
vocabulary differ on purpose (`order` vs `order_index`, `"user"` vs NULL,
`"45-60 min"` vs two integers). Round-tripping is the only thing that keeps the
two readings of one milestone from drifting.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "state.db")
os.environ["WARM_EMBEDDINGS"] = "false"

import pytest  # noqa: E402

from app.goals.state import (  # noqa: E402
    Milestone,
    format_effort,
    milestone_from_row,
    milestone_to_params,
    parse_effort,
    renumber,
)
from app.migrations import migrate  # noqa: E402


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    connection.execute("INSERT INTO goals (id, title) VALUES (1, 'Thermo midterm')")
    yield connection
    connection.close()


def insert(conn: sqlite3.Connection, milestone: Milestone, **kwargs) -> int:
    params = milestone_to_params(milestone, goal_id=1, **kwargs)
    columns = ", ".join(params)
    placeholders = ", ".join(f":{k}" for k in params)
    cur = conn.execute(
        f"INSERT INTO milestones ({columns}) VALUES ({placeholders})", params
    )
    return cur.lastrowid


def milestone(**overrides) -> Milestone:
    base: Milestone = {
        "id": "m-1",
        "title": "Rebuild entropy from the ground up",
        "description": "Work the derivation, then the problem set.",
        "order": 3,
        "status": "proposed",
        "reason": "Entropy is scoring weak (18/100)",
        "source": "materials",
        "related_topic_ids": [7],
        "source_chunk_ids": [11, 12, 13],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def row_for(conn: sqlite3.Connection, row_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM milestones WHERE id = ?", (row_id,)).fetchone()


# --------------------------------------------------------------------------
# effort
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lo,hi,expected",
    [
        (45, 60, "45–60 min"),
        (30, 30, "30 min"),
        (30, None, "30 min"),
        (None, 90, "90 min"),
        (None, None, None),
    ],
)
def test_format_effort(lo, hi, expected):
    assert format_effort(lo, hi) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("45–60 min", (45, 60)),  # en-dash, as rendered
        ("45-60 min", (45, 60)),  # hyphen, as a user would retype it
        ("45 to 60 minutes", (45, 60)),
        ("90 min", (90, 90)),
        ("about an hour", (None, None)),  # no digits: store nothing, invent nothing
        (None, (None, None)),
    ],
)
def test_parse_effort(text, expected):
    assert parse_effort(text) == expected


def test_effort_round_trips_through_a_row(conn):
    row_id = insert(conn, milestone(est_effort="45–60 min"))
    row = row_for(conn, row_id)
    assert (row["est_effort_min"], row["est_effort_max"]) == (45, 60)
    assert milestone_from_row(row)["est_effort"] == "45–60 min"


# --------------------------------------------------------------------------
# source / "user"
# --------------------------------------------------------------------------


def test_user_source_stores_null_and_reads_back_as_user(conn):
    """001's CHECK only allows materials|research. A CHECK passes on NULL, which
    is what lets a user-authored milestone exist without rewriting the table."""
    row_id = insert(conn, milestone(source="user", related_topic_ids=[]))
    row = row_for(conn, row_id)
    assert row["source"] is None
    assert milestone_from_row(row)["source"] == "user"


@pytest.mark.parametrize("source", ["materials", "research"])
def test_graph_sources_round_trip_unchanged(conn, source):
    row = row_for(conn, insert(conn, milestone(source=source)))
    assert row["source"] == source
    assert milestone_from_row(row)["source"] == source


def test_a_user_added_milestone_with_topics_is_an_ordinary_materials_row(conn):
    row = row_for(conn, insert(conn, milestone(source="materials", related_topic_ids=[2, 5])))
    assert milestone_from_row(row)["related_topic_ids"] == [2, 5]


# --------------------------------------------------------------------------
# order / provenance
# --------------------------------------------------------------------------


def test_order_maps_to_order_index(conn):
    row = row_for(conn, insert(conn, milestone(order=3)))
    assert row["order_index"] == 3
    assert milestone_from_row(row)["order"] == 3


def test_source_chunk_ids_survive_the_row(conn):
    row = row_for(conn, insert(conn, milestone(source_chunk_ids=[11, 12, 13])))
    assert milestone_from_row(row)["source_chunk_ids"] == [11, 12, 13]


def test_malformed_json_columns_do_not_crash_a_read(conn):
    row_id = insert(conn, milestone())
    conn.execute(
        "UPDATE milestones SET related_topic_ids = 'not json' WHERE id = ?", (row_id,)
    )
    assert milestone_from_row(row_for(conn, row_id))["related_topic_ids"] == []


def test_full_round_trip_is_lossless(conn):
    original = milestone(
        est_effort="45–60 min", reason_long="The long version of the reason."
    )
    restored = milestone_from_row(row_for(conn, insert(conn, original)))
    for key in original:
        assert restored[key] == original[key], key


# --------------------------------------------------------------------------
# unlocks_after: string id in state, integer FK in the row
# --------------------------------------------------------------------------


def test_unlocks_after_is_passed_in_not_read_from_the_row(conn):
    first = insert(conn, milestone(id="m-1", order=1))
    second = insert(conn, milestone(id="m-2", order=2, unlocks_after="m-1"))
    conn.execute(
        "UPDATE milestones SET unlocks_after_id = ? WHERE id = ?", (first, second)
    )
    row = row_for(conn, second)
    assert row["unlocks_after_id"] == first
    # The row alone cannot produce the state id -- the caller resolves it.
    assert "unlocks_after" not in milestone_from_row(row)
    assert milestone_from_row(row, unlocks_after="m-1")["unlocks_after"] == "m-1"


def test_rejecting_a_prerequisite_clears_the_pointer_rather_than_orphaning_it(conn):
    first = insert(conn, milestone(id="m-1", order=1))
    second = insert(conn, milestone(id="m-2", order=2))
    conn.execute(
        "UPDATE milestones SET unlocks_after_id = ? WHERE id = ?", (first, second)
    )
    conn.execute("DELETE FROM milestones WHERE id = ?", (first,))
    assert row_for(conn, second)["unlocks_after_id"] is None


# --------------------------------------------------------------------------
# renumber
# --------------------------------------------------------------------------


def test_renumber_is_contiguous_and_skips_rejected():
    milestones = [
        milestone(id="a", order=1),
        milestone(id="b", order=2, status="rejected"),
        milestone(id="c", order=3),
        milestone(id="d", order=4),
    ]
    renumber(milestones)
    assert [m["order"] for m in milestones if m["status"] != "rejected"] == [1, 2, 3]


def test_renumber_after_a_reorder():
    milestones = [milestone(id="a", order=1), milestone(id="b", order=2)]
    milestones.reverse()
    renumber(milestones)
    assert [(m["id"], m["order"]) for m in milestones] == [("b", 1), ("a", 2)]
