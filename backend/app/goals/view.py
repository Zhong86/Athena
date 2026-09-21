"""Rows -> the response models the pages render.

Kept out of the router so the shape of a goal has one definition, and out of the
repository so SQL stays free of presentation concerns. Everything here is a read.
"""

import json
import sqlite3
from typing import Any

from app.goals import repository as repo
from app.goals.schemas import (
    GoalCard,
    GoalDetail,
    MilestoneOut,
    SourceChunk,
    TopicStrength,
)
from app.goals.state import format_effort
from app.materials import repository as materials_repo
from app.ranking import band


def _percent(done: int, total: int) -> int:
    """Computed, never stored -- a stored percent drifts the first time a
    milestone is added or removed."""
    return round(done * 100 / total) if total else 0


def _chunk_ids(rows: list[sqlite3.Row]) -> list[int]:
    ids: list[int] = []
    for row in rows:
        try:
            ids.extend(json.loads(row["source_chunk_ids"] or "[]"))
        except ValueError:
            continue
    return ids


def milestones_out(conn: sqlite3.Connection, goal_id: int) -> list[MilestoneOut]:
    """Every milestone, fully detailed.

    One `get_chunks` call for the whole goal rather than one per milestone: the
    detail page expands its focus milestone on arrival, so provenance is on the
    critical path and must not cost N queries.
    """
    rows = repo.list_milestone_rows(conn, goal_id)
    if not rows:
        return []

    chunks = materials_repo.get_chunks(conn, _chunk_ids(rows))
    titles = {row["id"]: row["title"] for row in rows}

    out: list[MilestoneOut] = []
    for row in rows:
        ids = json.loads(row["source_chunk_ids"] or "[]")
        hydrated = [
            SourceChunk(
                chunk_id=chunk["id"],
                topic_id=chunk["topic_id"],
                topic_name=chunk["topic_name"],
                source_file_id=chunk["source_file_id"],
                source_filename=chunk["source_filename"],
            )
            # A chunk whose row is gone (its file was deleted) is skipped rather
            # than rendered as a dead link -- SQLite is the source of truth.
            for chunk in (chunks.get(cid) for cid in ids)
            if chunk is not None
        ]
        out.append(
            MilestoneOut(
                id=row["id"],
                state_id=row["state_id"],
                title=row["title"],
                description=row["description"],
                order=row["order_index"],
                status=row["status"],
                progress_status=row["progress_status"],
                reason=row["reason"],
                reason_long=row["reason_long"],
                est_effort=format_effort(row["est_effort_min"], row["est_effort_max"]),
                unlocks_after_title=titles.get(row["unlocks_after_id"]),
                source=row["source"] or "user",
                related_topic_ids=json.loads(row["related_topic_ids"] or "[]"),
                source_chunks=hydrated,
            )
        )
    return out


def topic_strengths(
    conn: sqlite3.Connection, milestones: list[MilestoneOut]
) -> list[TopicStrength]:
    """The detail page's Topic strength chips, limited to this goal's topics.

    Showing every topic in the system would make the section about Materials
    rather than about this roadmap.
    """
    topic_ids: list[int] = []
    for milestone in milestones:
        for topic_id in milestone.related_topic_ids:
            if topic_id not in topic_ids:
                topic_ids.append(topic_id)
    if not topic_ids:
        return []

    placeholders = ",".join("?" * len(topic_ids))
    rows = conn.execute(
        f"SELECT id, name, user_understanding FROM topics WHERE id IN ({placeholders}) "
        "ORDER BY user_understanding",
        topic_ids,
    ).fetchall()
    return [
        TopicStrength(
            topic_id=row["id"],
            name=row["name"],
            user_understanding=row["user_understanding"],
            strength=band(row["user_understanding"]),
        )
        for row in rows
    ]


def goal_card(conn: sqlite3.Connection, goal: dict[str, Any]) -> GoalCard:
    done, total = repo.progress(conn, goal["id"])
    focus = conn.execute(
        "SELECT title FROM milestones WHERE goal_id = ? AND progress_status = 'current' "
        "ORDER BY order_index LIMIT 1",
        (goal["id"],),
    ).fetchone()
    return GoalCard(
        id=goal["id"],
        title=goal["title"],
        short_name=goal["short_name"],
        status=goal["status"],
        category=goal["category"],
        course_code=goal["course_code"],
        due_at=goal["due_at"],
        percent=_percent(done, total),
        done_count=done,
        total_count=total,
        created_at=goal["created_at"],
        focus_title=focus["title"] if focus else None,
    )


def goal_detail(conn: sqlite3.Connection, goal: dict[str, Any]) -> GoalDetail:
    milestones = milestones_out(conn, goal["id"])
    done, total = repo.progress(conn, goal["id"])
    return GoalDetail(
        id=goal["id"],
        title=goal["title"],
        short_name=goal["short_name"],
        description=goal["description"],
        status=goal["status"],
        category=goal["category"],
        course_code=goal["course_code"],
        due_at=goal["due_at"],
        derivation=goal["derivation"],
        order_rationale=goal["order_rationale"],
        created_at=goal["created_at"],
        updated_at=goal["updated_at"],
        percent=_percent(done, total),
        done_count=done,
        total_count=total,
        milestones=milestones,
        topic_strengths=topic_strengths(conn, milestones),
    )
