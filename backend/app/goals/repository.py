"""SQL for `goals`, `milestones` and `roadmap_runs`.

Two things here that are not plain CRUD:

`insert_roadmap` writes a whole approved roadmap in one call, because a goal with
half a roadmap is worse than no goal at all. It also resolves `unlocks_after`
(a state id) to `unlocks_after_id` (a row id) in a second pass -- the mapping
does not exist until every row has been inserted.

`reorder` renumbers in one statement per row inside one transaction, because a
half-applied reorder leaves two milestones sharing an `order_index` and the
detail page then renders them in arbitrary order.
"""

import sqlite3
from typing import Any

from app.clock import utc_now_iso
from app.goals.state import Milestone, milestone_from_row, milestone_to_params, parse_effort

# Exactly 001's CHECK. The goal list mockup also styles a Paused pill, but
# SQLite cannot widen a CHECK constraint without rewriting the table, and
# rewriting `goals` means dropping it -- which, with foreign_keys ON, cascades
# every milestone row away. `archived` covers "not active" for now; if Paused
# becomes load-bearing it needs a real rebuild migration, not a wider tuple here.
GOAL_STATUSES = ("draft", "committed", "archived")
RUN_STATUSES = (
    "clarifying",
    "decomposing",
    "awaiting_approval",
    "committed",
    "abandoned",
)
PROGRESS_STATUSES = ("upcoming", "current", "done")

_GOAL_FIELDS = (
    "title",
    "short_name",
    "course_code",
    "category",
    "due_at",
    "description",
    "derivation",
    "order_rationale",
    "status",
)


# --------------------------------------------------------------------------
# goals
# --------------------------------------------------------------------------


def create_goal(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    payload = {k: v for k, v in fields.items() if k in _GOAL_FIELDS}
    payload.setdefault("title", "Untitled goal")
    payload["updated_at"] = utc_now_iso()
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{k}" for k in payload)
    cur = conn.execute(
        f"INSERT INTO goals ({columns}) VALUES ({placeholders}) RETURNING *", payload
    )
    return dict(cur.fetchone())


def get_goal(conn: sqlite3.Connection, goal_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return dict(row) if row else None


def list_goals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Newest first. Draft goals are excluded: a run abandoned mid-approval must
    not leave a half-built card on the goal list."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM goals WHERE status != 'draft' ORDER BY created_at DESC, id DESC"
        )
    ]


def update_goal(conn: sqlite3.Connection, goal_id: int, **fields: Any) -> dict[str, Any] | None:
    payload = {k: v for k, v in fields.items() if k in _GOAL_FIELDS and v is not None}
    if "status" in payload and payload["status"] not in GOAL_STATUSES:
        raise ValueError(f"unknown goal status: {payload['status']}")
    if not payload:
        return get_goal(conn, goal_id)
    payload["updated_at"] = utc_now_iso()
    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    cur = conn.execute(
        f"UPDATE goals SET {assignments} WHERE id = :id RETURNING *",
        {**payload, "id": goal_id},
    )
    row = cur.fetchone()
    return dict(row) if row else None


def touch_goal(conn: sqlite3.Connection, goal_id: int) -> None:
    """Keeps "Last updated 2 minutes ago" honest after a milestone-level edit."""
    conn.execute(
        "UPDATE goals SET updated_at = ? WHERE id = ?", (utc_now_iso(), goal_id)
    )


def delete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------
# milestones
# --------------------------------------------------------------------------


def list_milestone_rows(conn: sqlite3.Connection, goal_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM milestones WHERE goal_id = ? ORDER BY order_index, id",
            (goal_id,),
        )
    )


def list_milestones(conn: sqlite3.Connection, goal_id: int) -> list[Milestone]:
    """Rows as graph-shaped milestones, with `unlocks_after` resolved.

    Resolution happens here rather than in `milestone_from_row` because it needs
    every row in the goal at once: the FK points at a row id and state speaks in
    state ids.
    """
    rows = list_milestone_rows(conn, goal_id)
    state_ids = {row["id"]: row["state_id"] or f"row-{row['id']}" for row in rows}
    return [
        milestone_from_row(row, unlocks_after=state_ids.get(row["unlocks_after_id"]))
        for row in rows
    ]


def insert_roadmap(
    conn: sqlite3.Connection, goal_id: int, milestones: list[Milestone]
) -> list[int]:
    """Insert an approved roadmap in order. Returns the new row ids.

    The first milestone becomes `current`, the rest `upcoming` -- a committed
    roadmap with nothing in focus gives the detail page no "Focus now" item.
    """
    row_ids: list[int] = []
    by_state_id: dict[str, int] = {}

    for position, milestone in enumerate(milestones):
        params = milestone_to_params(
            milestone,
            goal_id,
            progress_status="current" if position == 0 else "upcoming",
        )
        columns = ", ".join(params)
        placeholders = ", ".join(f":{k}" for k in params)
        cur = conn.execute(
            f"INSERT INTO milestones ({columns}) VALUES ({placeholders})", params
        )
        row_ids.append(cur.lastrowid)
        by_state_id[milestone["id"]] = cur.lastrowid

    # Second pass: every row exists now, so state ids can be resolved to FKs.
    # A pointer at a milestone that was rejected (or at a later one, which would
    # be nonsense) resolves to nothing and is left NULL rather than failing the
    # whole commit.
    positions = {milestone["id"]: i for i, milestone in enumerate(milestones)}
    for position, (row_id, milestone) in enumerate(zip(row_ids, milestones)):
        target = milestone.get("unlocks_after")
        if not target or target not in by_state_id:
            continue
        if positions[target] >= position:
            continue  # self- or forward reference: nonsense, so drop it
        conn.execute(
            "UPDATE milestones SET unlocks_after_id = ? WHERE id = ?",
            (by_state_id[target], row_id),
        )

    return row_ids


def add_milestone(
    conn: sqlite3.Connection, goal_id: int, milestone: Milestone, *, position: int | None = None
) -> dict[str, Any]:
    """Insert one milestone post-commit, shifting later ones down."""
    rows = list_milestone_rows(conn, goal_id)
    order_index = position if position is not None else len(rows) + 1
    conn.execute(
        "UPDATE milestones SET order_index = order_index + 1 "
        "WHERE goal_id = ? AND order_index >= ?",
        (goal_id, order_index),
    )
    params = milestone_to_params(milestone, goal_id)
    params["order_index"] = order_index
    columns = ", ".join(params)
    placeholders = ", ".join(f":{k}" for k in params)
    cur = conn.execute(
        f"INSERT INTO milestones ({columns}) VALUES ({placeholders}) RETURNING *", params
    )
    touch_goal(conn, goal_id)
    return dict(cur.fetchone())


_MILESTONE_EDITABLE = ("title", "description", "reason", "reason_long", "progress_status")


def update_milestone(
    conn: sqlite3.Connection, goal_id: int, milestone_id: int, **fields: Any
) -> dict[str, Any] | None:
    payload = {k: v for k, v in fields.items() if k in _MILESTONE_EDITABLE and v is not None}
    if "progress_status" in payload and payload["progress_status"] not in PROGRESS_STATUSES:
        raise ValueError(f"unknown progress status: {payload['progress_status']}")
    if (effort := fields.get("est_effort")) is not None:
        payload["est_effort_min"], payload["est_effort_max"] = parse_effort(effort)
    if not payload:
        row = conn.execute(
            "SELECT * FROM milestones WHERE id = ? AND goal_id = ?", (milestone_id, goal_id)
        ).fetchone()
        return dict(row) if row else None

    assignments = ", ".join(f"{k} = :{k}" for k in payload)
    cur = conn.execute(
        f"UPDATE milestones SET {assignments} WHERE id = :id AND goal_id = :goal_id "
        "RETURNING *",
        {**payload, "id": milestone_id, "goal_id": goal_id},
    )
    row = cur.fetchone()
    if row is None:
        return None
    if "progress_status" in payload:
        _settle_focus(conn, goal_id, milestone_id, payload["progress_status"])
        row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    touch_goal(conn, goal_id)
    return dict(row)


def _settle_focus(
    conn: sqlite3.Connection, goal_id: int, milestone_id: int, progress_status: str
) -> None:
    """Keep "exactly one current" true after a progress change.

    `delete_milestone` already promotes a successor when it removes the focus;
    the same invariant has to hold here or the detail page renders two "Focus
    now" pills (two milestones set current) or none at all (the focus marked
    done). Promotion picks the first `upcoming` by order, which is the same rule
    commit_roadmap used to pick the first focus.
    """
    if progress_status == "current":
        conn.execute(
            "UPDATE milestones SET progress_status = 'upcoming' "
            "WHERE goal_id = ? AND id != ? AND progress_status = 'current'",
            (goal_id, milestone_id),
        )
        return

    still_current = conn.execute(
        "SELECT 1 FROM milestones WHERE goal_id = ? AND progress_status = 'current' LIMIT 1",
        (goal_id,),
    ).fetchone()
    if still_current:
        return
    successor = conn.execute(
        "SELECT id FROM milestones WHERE goal_id = ? AND progress_status = 'upcoming' "
        "ORDER BY order_index LIMIT 1",
        (goal_id,),
    ).fetchone()
    if successor:
        conn.execute(
            "UPDATE milestones SET progress_status = 'current' WHERE id = ?",
            (successor["id"],),
        )


def delete_milestone(conn: sqlite3.Connection, goal_id: int, milestone_id: int) -> bool:
    """Deleting the focus milestone promotes the next one.

    Otherwise a goal can end up with nothing marked `current`, and the detail
    page loses its "Focus now" item and its two action buttons with it.
    """
    row = conn.execute(
        "SELECT * FROM milestones WHERE id = ? AND goal_id = ?", (milestone_id, goal_id)
    ).fetchone()
    if row is None:
        return False

    conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    _renumber(conn, goal_id)

    if row["progress_status"] == "current":
        successor = conn.execute(
            "SELECT id FROM milestones WHERE goal_id = ? AND progress_status = 'upcoming' "
            "ORDER BY order_index LIMIT 1",
            (goal_id,),
        ).fetchone()
        if successor:
            conn.execute(
                "UPDATE milestones SET progress_status = 'current' WHERE id = ?",
                (successor["id"],),
            )
    touch_goal(conn, goal_id)
    return True


def reorder(conn: sqlite3.Connection, goal_id: int, ids_in_order: list[int]) -> bool:
    """Renumber to exactly `ids_in_order`. All or nothing.

    Rejects a partial list rather than renumbering what it was given: the caller
    sending 4 of 7 ids has a bug, and silently leaving three milestones at stale
    positions produces duplicate `order_index` values.
    """
    existing = [row["id"] for row in list_milestone_rows(conn, goal_id)]
    if sorted(existing) != sorted(ids_in_order):
        return False
    for position, milestone_id in enumerate(ids_in_order, start=1):
        conn.execute(
            "UPDATE milestones SET order_index = ? WHERE id = ? AND goal_id = ?",
            (position, milestone_id, goal_id),
        )
    touch_goal(conn, goal_id)
    return True


def _renumber(conn: sqlite3.Connection, goal_id: int) -> None:
    for position, row in enumerate(list_milestone_rows(conn, goal_id), start=1):
        if row["order_index"] != position:
            conn.execute(
                "UPDATE milestones SET order_index = ? WHERE id = ?", (position, row["id"])
            )


def progress(conn: sqlite3.Connection, goal_id: int) -> tuple[int, int]:
    """(done, total). Percent is computed from this and never stored -- a stored
    percent drifts the first time a milestone is added or removed."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN progress_status = 'done' THEN 1 ELSE 0 END) AS done "
        "FROM milestones WHERE goal_id = ?",
        (goal_id,),
    ).fetchone()
    return int(row["done"] or 0), int(row["total"] or 0)


# --------------------------------------------------------------------------
# roadmap_runs -- the index the HTTP layer looks in-flight graph runs up by
# --------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection, *, thread_id: str, raw_goal_input: str, status: str = "clarifying"
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO roadmap_runs (thread_id, raw_goal_input, status) VALUES (?, ?, ?) "
        "RETURNING *",
        (thread_id, raw_goal_input, status),
    )
    return dict(cur.fetchone())


def get_run(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM roadmap_runs WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return dict(row) if row else None


def list_unfinished_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Runs the student can still come back to.

    Without this the "Save and exit" link is a trapdoor: the thread id only ever
    lived in that page's URL, so a parked interrupt would be unreachable the
    moment the tab closed.
    """
    rows = conn.execute(
        "SELECT * FROM roadmap_runs WHERE status NOT IN ('committed', 'abandoned') "
        "ORDER BY updated_at DESC, thread_id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def set_run_status(
    conn: sqlite3.Connection, thread_id: str, status: str, *, goal_id: int | None = None
) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run status: {status}")
    conn.execute(
        "UPDATE roadmap_runs SET status = ?, updated_at = ?, "
        "goal_id = COALESCE(?, goal_id) WHERE thread_id = ?",
        (status, utc_now_iso(), goal_id, thread_id),
    )
