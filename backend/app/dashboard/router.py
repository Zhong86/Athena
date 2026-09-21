from fastapi import APIRouter

from app.dashboard.schemas import CheckIn, DashboardResponse, PriorityItem, WeakTopic
from app.db import connection
from app.materials import repository as materials_repo
from app.quizzes import repository as quizzes_repo
from app.ranking import rank_topics

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard() -> DashboardResponse:
    """Live version of `dashboard.html` -- Step 7 of the implementation plan."""
    with connection() as conn:
        topics = materials_repo.list_topics(conn)
        latest_event = quizzes_repo.latest_understanding_event(conn)

    signals = rank_topics(topics)

    check_in = (
        CheckIn(
            topic_id=latest_event["topic_id"],
            topic_name=latest_event["topic_name"],
            source=latest_event["source"],
            previous_understanding=latest_event["previous_understanding"],
            understanding=latest_event["understanding"],
            reason=latest_event["reason"],
            evidence=latest_event["evidence"],
            created_at=latest_event["created_at"],
        )
        if latest_event is not None
        else None
    )

    return DashboardResponse(
        cold_start=latest_event is None,
        check_in=check_in,
        weak_topics=[
            WeakTopic(topic_id=s.topic_id, name=s.topic_name, understanding=s.understanding)
            for s in signals
            if s.band == "weak"
        ],
        priority_feed=[
            PriorityItem(
                topic_id=s.topic_id,
                name=s.topic_name,
                understanding=s.understanding,
                band=s.band,
                reason=s.reason,
                score=s.score,
            )
            for s in signals
        ],
    )
