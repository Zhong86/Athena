from typing import Any

from pydantic import BaseModel

from app.quizzes.schemas import UnderstandingSource


class CheckIn(BaseModel):
    """Most recent understanding_events row, across every topic."""

    topic_id: int
    topic_name: str
    source: UnderstandingSource
    previous_understanding: int
    understanding: int
    reason: str
    evidence: dict[str, Any] = {}
    created_at: str


class PriorityItem(BaseModel):
    """One `app.ranking.TopicSignal`, shaped for the feed."""

    topic_id: int
    name: str
    understanding: int
    band: str
    reason: str
    score: float


class WeakTopic(BaseModel):
    topic_id: int
    name: str
    understanding: int


class DashboardResponse(BaseModel):
    # True when no quiz/session has ever produced a score -- fresh install,
    # nothing to summarize yet.
    cold_start: bool
    check_in: CheckIn | None = None
    weak_topics: list[WeakTopic] = []
    priority_feed: list[PriorityItem] = []
