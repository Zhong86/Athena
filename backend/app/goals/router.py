"""HTTP surface for Goals.

Three endpoints drive the graph (start / resume / read); the rest is plain CRUD.
"Adjust roadmap" is that CRUD, not a second graph run -- re-entering the creation
screen from a committed goal edits rows directly, with no thread and no
checkpointer involved.

The graph runs in a worker thread: LangGraph's SqliteSaver is sync, and the rest
of this backend already calls sync `sqlite3` from async routes. One concurrency
model beats two.
"""

import logging
import uuid

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.db import connection
from app.goals import repository as repo
from app.goals import view
from app.goals.graph import compiled
from app.goals.llm import LLMUnavailable
from app.goals.schemas import (
    GoalCard,
    GoalDetail,
    GoalUpdate,
    MilestoneCreate,
    MilestoneOut,
    MilestoneUpdate,
    ReorderRequest,
    ResumeRoadmap,
    RoadmapEnvelope,
    RoadmapRunCard,
    StartRoadmap,
)
from app.goals.state import Milestone, new_state
from app.materials import repository as materials_repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/goals", tags=["goals"])


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------


def _envelope(thread_id: str, result: dict) -> RoadmapEnvelope:
    """The one response shape every graph endpoint returns."""
    interrupts = result.get("__interrupt__") or ()
    payload = interrupts[0].value if interrupts else None
    goal_id = result.get("goal_id")
    status = result.get("status") or ("committed" if goal_id else "clarifying")
    return RoadmapEnvelope(
        thread_id=thread_id,
        status=status,
        interrupt=payload,
        goal_id=goal_id,
        raw_goal_input=result.get("raw_goal_input"),
    )


async def _invoke(thread_id: str, payload) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    try:
        return await anyio.to_thread.run_sync(
            lambda: compiled().invoke(payload, config=config)
        )
    except LLMUnavailable as exc:
        # Decomposition cannot degrade -- a roadmap is the product -- so this is
        # the one place a dead gateway has to reach the user.
        raise HTTPException(status_code=503, detail=f"Hermes unavailable: {exc}") from exc


@router.post("/roadmap", response_model=RoadmapEnvelope)
async def start_roadmap(body: StartRoadmap) -> RoadmapEnvelope:
    thread_id = f"roadmap-{uuid.uuid4().hex[:12]}"
    with connection() as conn:
        repo.create_run(conn, thread_id=thread_id, raw_goal_input=body.raw_goal_input)

    result = await _invoke(
        thread_id, new_state(body.raw_goal_input, session_id=body.session_id)
    )
    envelope = _envelope(thread_id, result)
    with connection() as conn:
        repo.set_run_status(conn, thread_id, envelope.status, goal_id=envelope.goal_id)
    return envelope


@router.post("/roadmap/{thread_id}/resume", response_model=RoadmapEnvelope)
async def resume_roadmap(thread_id: str, body: ResumeRoadmap) -> RoadmapEnvelope:
    with connection() as conn:
        run = repo.get_run(conn, thread_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such roadmap run")
    if run["status"] in ("committed", "abandoned"):
        # Resuming a finished run would start a second pass over commit_roadmap
        # and produce a duplicate goal.
        raise HTTPException(
            status_code=409,
            detail=f"This run is already {run['status']}",
        )

    result = await _invoke(thread_id, Command(resume=body.payload))
    envelope = _envelope(thread_id, result)
    with connection() as conn:
        repo.set_run_status(conn, thread_id, envelope.status, goal_id=envelope.goal_id)
    return envelope


@router.get("/roadmap", response_model=list[RoadmapRunCard])
async def list_unfinished_runs() -> list[RoadmapRunCard]:
    """Drafts still in progress. Declared before `/roadmap/{thread_id}` for
    readability only -- the path is literal, so ordering does not decide it."""
    with connection() as conn:
        return [RoadmapRunCard(**run) for run in repo.list_unfinished_runs(conn)]


@router.get("/roadmap/{thread_id}", response_model=RoadmapEnvelope)
async def get_roadmap_run(thread_id: str) -> RoadmapEnvelope:
    """Current state of a run -- what "Save and exit" comes back to."""
    with connection() as conn:
        run = repo.get_run(conn, thread_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such roadmap run")

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await anyio.to_thread.run_sync(lambda: compiled().get_state(config))
    pending = snapshot.tasks[0].interrupts if snapshot.tasks else ()
    return RoadmapEnvelope(
        thread_id=thread_id,
        status=snapshot.values.get("status", run["status"]),
        interrupt=pending[0].value if pending else None,
        goal_id=snapshot.values.get("goal_id") or run["goal_id"],
        raw_goal_input=snapshot.values.get("raw_goal_input") or run["raw_goal_input"],
    )


@router.delete("/roadmap/{thread_id}", status_code=204)
async def abandon_roadmap(thread_id: str) -> None:
    """Abandon a draft run. The checkpoint is left in place deliberately: it is
    small, and keeping it means an accidental abandon is recoverable by hand."""
    with connection() as conn:
        if repo.get_run(conn, thread_id) is None:
            raise HTTPException(status_code=404, detail="No such roadmap run")
        repo.set_run_status(conn, thread_id, "abandoned")


# --------------------------------------------------------------------------
# committed goals
# --------------------------------------------------------------------------


@router.get("", response_model=list[GoalCard])
async def list_goals() -> list[GoalCard]:
    with connection() as conn:
        return [view.goal_card(conn, goal) for goal in repo.list_goals(conn)]


@router.get("/{goal_id}", response_model=GoalDetail)
async def get_goal(goal_id: int) -> GoalDetail:
    with connection() as conn:
        goal = repo.get_goal(conn, goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail="No such goal")
        return view.goal_detail(conn, goal)


@router.patch("/{goal_id}", response_model=GoalDetail)
async def update_goal(goal_id: int, body: GoalUpdate) -> GoalDetail:
    with connection() as conn:
        if repo.get_goal(conn, goal_id) is None:
            raise HTTPException(status_code=404, detail="No such goal")
        try:
            goal = repo.update_goal(conn, goal_id, **body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return view.goal_detail(conn, goal)


# --------------------------------------------------------------------------
# milestones -- this is what "Adjust roadmap" drives
# --------------------------------------------------------------------------


def _ensure_topic(conn, name: str) -> int:
    """Find-or-create, with `auto_created = 0` for a student-named topic.

    Shares the rule with the graph's `add_milestone` but not the code: that one
    opens its own connection because a node has none, this one is already inside
    the request's transaction and must not open a second.
    """
    existing = materials_repo.find_topic_by_name(conn, name)
    if existing:
        return int(existing["id"])
    return int(materials_repo.create_topic(conn, name=name, auto_created=False)["id"])


@router.post("/{goal_id}/milestones", response_model=MilestoneOut, status_code=201)
async def add_milestone(goal_id: int, body: MilestoneCreate) -> MilestoneOut:
    with connection() as conn:
        if repo.get_goal(conn, goal_id) is None:
            raise HTTPException(status_code=404, detail="No such goal")

        topic_ids = list(body.related_topic_ids)
        if body.new_topic_name and body.new_topic_name.strip():
            topic_ids.append(_ensure_topic(conn, body.new_topic_name.strip()))

        milestone: Milestone = {
            "id": "",  # a post-commit addition has no draft to trace back to
            "title": body.title,
            "description": body.description or "",
            "order": body.position or 0,
            "status": "edited",
            "reason": body.reason or "Added by you.",
            # Same rule as the graph's add_milestone: topics attached makes it an
            # ordinary materials row, nothing attached makes it user-authored.
            "source": "materials" if topic_ids else "user",
            "related_topic_ids": topic_ids,
            "source_chunk_ids": [],
        }
        if body.est_effort:
            milestone["est_effort"] = body.est_effort

        row = repo.add_milestone(conn, goal_id, milestone, position=body.position)
        return next(m for m in view.milestones_out(conn, goal_id) if m.id == row["id"])


@router.patch("/{goal_id}/milestones/{milestone_id}", response_model=MilestoneOut)
async def update_milestone(
    goal_id: int, milestone_id: int, body: MilestoneUpdate
) -> MilestoneOut:
    with connection() as conn:
        try:
            row = repo.update_milestone(
                conn, goal_id, milestone_id, **body.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="No such milestone")
        return next(m for m in view.milestones_out(conn, goal_id) if m.id == milestone_id)


@router.delete("/{goal_id}/milestones/{milestone_id}", status_code=204)
async def delete_milestone(goal_id: int, milestone_id: int) -> None:
    with connection() as conn:
        if not repo.delete_milestone(conn, goal_id, milestone_id):
            raise HTTPException(status_code=404, detail="No such milestone")


@router.put("/{goal_id}/milestones/order", response_model=list[MilestoneOut])
async def reorder_milestones(goal_id: int, body: ReorderRequest) -> list[MilestoneOut]:
    """Renumber in one transaction. A partial list is rejected rather than
    applied: leaving some milestones at stale positions produces duplicate
    `order_index` values, and the page then renders them in arbitrary order."""
    with connection() as conn:
        if repo.get_goal(conn, goal_id) is None:
            raise HTTPException(status_code=404, detail="No such goal")
        if not repo.reorder(conn, goal_id, body.ids_in_order):
            raise HTTPException(
                status_code=422,
                detail="ids_in_order must list every milestone of this goal exactly once",
            )
        return view.milestones_out(conn, goal_id)
