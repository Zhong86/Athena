"""Request/response models for `/goals`.

One `MilestoneOut` serves both the approval screen and the detail page. There is
deliberately no trimmed variant: every roadmap item on the detail page is an
accordion and the focus milestone renders expanded on arrival, so a thinner shape
would have to be widened the first time anything expands -- which is always.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

GoalStatus = Literal["draft", "committed", "archived"]
GoalCategory = Literal["academic", "career"]
MilestoneStatus = Literal["proposed", "approved", "edited", "rejected"]
MilestoneSource = Literal["materials", "research", "user"]
ProgressStatus = Literal["upcoming", "current", "done"]
Strength = Literal["unknown", "weak", "fair", "strong"]


class SourceChunk(BaseModel):
    """Provenance, hydrated. Backs both the accordion's `N chunks tagged
    "Topic"` line and the focus milestone's "See related materials"."""

    chunk_id: int
    topic_id: int | None = None
    topic_name: str | None = None
    source_file_id: int
    source_filename: str


class MilestoneOut(BaseModel):
    id: int
    state_id: str | None = None
    title: str
    description: str | None = None
    order: int
    status: MilestoneStatus
    progress_status: ProgressStatus
    # Collapsed row.
    reason: str | None = None
    # Everything below is the expanded accordion.
    reason_long: str | None = None
    est_effort: str | None = None
    unlocks_after_title: str | None = None
    source: MilestoneSource
    related_topic_ids: list[int] = []
    source_chunks: list[SourceChunk] = []


class TopicStrength(BaseModel):
    topic_id: int
    name: str
    user_understanding: int
    strength: Strength


class GoalCard(BaseModel):
    """One row of the goal list."""

    id: int
    title: str
    short_name: str | None = None
    status: GoalStatus
    category: GoalCategory
    course_code: str | None = None
    due_at: str | None = None
    percent: int
    done_count: int
    total_count: int
    created_at: str
    # The current focus milestone's title, for the card's summary line.
    focus_title: str | None = None


class GoalDetail(BaseModel):
    id: int
    title: str
    short_name: str | None = None
    description: str | None = None
    status: GoalStatus
    category: GoalCategory
    course_code: str | None = None
    due_at: str | None = None
    derivation: str | None = None
    order_rationale: str | None = None
    created_at: str
    updated_at: str | None = None
    percent: int
    done_count: int
    total_count: int
    milestones: list[MilestoneOut] = []
    topic_strengths: list[TopicStrength] = []


# --------------------------------------------------------------------------
# graph driving
# --------------------------------------------------------------------------


class StartRoadmap(BaseModel):
    raw_goal_input: str = Field(min_length=1, max_length=2000)
    session_id: str = ""


class ResumeRoadmap(BaseModel):
    """The resume payload, passed to the graph as-is.

    Untyped on purpose: the clarify interrupt takes answers and the approval
    interrupt takes one of five actions, and the node that called `interrupt()`
    is the only thing that knows which. Validating the union here would mean
    keeping a second copy of every action's shape in sync with `approve.py`.
    """

    payload: Any


class RoadmapRunCard(BaseModel):
    """An unfinished run, for the "pick up where you left off" row on /goal."""

    thread_id: str
    status: str
    raw_goal_input: str
    created_at: str
    updated_at: str | None = None


class RoadmapEnvelope(BaseModel):
    """One shape for every graph endpoint, so the frontend has one code path.

    `interrupt` null with a `goal_id` set means the run committed.
    """

    thread_id: str
    status: str
    interrupt: dict[str, Any] | None = None
    goal_id: int | None = None
    # Echoed back so a reopened run can redraw the student's own opening line;
    # it is the one thing the interrupt payloads do not carry.
    raw_goal_input: str | None = None


# --------------------------------------------------------------------------
# post-commit CRUD ("Adjust roadmap")
# --------------------------------------------------------------------------


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    short_name: str | None = None
    status: GoalStatus | None = None
    due_at: str | None = None
    description: str | None = None


class MilestoneCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = None
    reason: str | None = None
    est_effort: str | None = None
    related_topic_ids: list[int] = []
    # A topic the student named rather than picked. Created with auto_created=0,
    # same as the graph's add_milestone action.
    new_topic_name: str | None = Field(default=None, max_length=120)
    # 1-based; omitted appends.
    position: int | None = Field(default=None, ge=1)


class MilestoneUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    reason: str | None = None
    reason_long: str | None = None
    est_effort: str | None = None
    progress_status: ProgressStatus | None = None


class ReorderRequest(BaseModel):
    ids_in_order: list[int] = Field(min_length=1)
