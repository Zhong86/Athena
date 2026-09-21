"""Weak topic ranking -- one implementation, two callers.

Step 6's `personalize_decomposition` orders roadmap milestones with this, and
Step 7's Dashboard Priority Feed ranks topics with it. The implementation plan is
explicit that these must be the same function rather than two rankers that agree
by coincidence, so the shared part lives at `app/` level and neither feature owns
it.

Ranking is on measured weakness alone. Deadline-aware ranking was designed here
and cut (Zhong, 2026-09-21): Athena does not track the student's tasks, so there
is no due-date signal to read and none to invent.

Every ranked result carries the sentence that explains it. A score with no
traceable reason is the failure mode to avoid here: the whole point of the
feature is that the user can see *why* something moved up.

Band thresholds match `frontend/src/lib/api.ts:understandingBand` (40 / 70). They
are duplicated rather than derived because the backend needs them to generate
copy and the frontend needs them to pick a colour; if they drift, the number on
screen stops matching the word next to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

WEAK_BELOW = 40
FAIR_BELOW = 70
# No signal yet. Emphatically *not* the same as weak: a fresh install would
# otherwise produce a roadmap claiming evidence it does not have.
NO_SIGNAL = -1


def band(score: int) -> str:
    """"unknown" | "weak" | "fair" | "strong" -- mirrors the frontend's bands."""
    if score < 0:
        return "unknown"
    if score < WEAK_BELOW:
        return "weak"
    if score < FAIR_BELOW:
        return "fair"
    return "strong"


@dataclass
class TopicSignal:
    """One topic's priority, plus the evidence that produced it."""

    topic_id: int
    topic_name: str
    understanding: int
    score: float
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def band(self) -> str:
        return band(self.understanding)


def _need(understanding: int) -> float:
    """How much work a topic needs. Unknown sits between weak and fair.

    An unscored topic is worth surfacing (we should find out) but must not
    outrank a topic measured as weak (we know).
    """
    if understanding == NO_SIGNAL:
        return 0.55
    return 1.0 - (max(0, min(100, understanding)) / 100)


def _reason(name: str, understanding: int) -> str:
    """The sentence shown next to the ranking. Only states what was measured."""
    tone = band(understanding)
    if tone == "unknown":
        return f"{name} has no check-in signal yet"
    if tone == "weak":
        return f"{name} is scoring weak ({understanding}/100)"
    if tone == "fair":
        return f"{name} is still building ({understanding}/100)"
    return f"{name} is solid ({understanding}/100)"


def rank_topics(topics: Iterable[dict[str, Any]]) -> list[TopicSignal]:
    """Topics, most worth attention first. Dashboard's Priority Feed calls this.

    `topics` rows need `id`, `name`, `user_understanding`.
    """
    signals: list[TopicSignal] = []
    for topic in topics:
        understanding = int(topic.get("user_understanding", NO_SIGNAL))
        need = _need(understanding)
        signals.append(
            TopicSignal(
                topic_id=int(topic["id"]),
                topic_name=topic["name"],
                understanding=understanding,
                score=round(need, 4),
                reason=_reason(topic["name"], understanding),
                evidence={"need": round(need, 4)},
            )
        )
    signals.sort(key=lambda s: (-s.score, s.topic_name))
    return signals


def rank_keyed(
    items: Sequence[tuple[str, Sequence[int]]],
    signals: Sequence[TopicSignal],
) -> list[tuple[str, float, str | None]]:
    """Order arbitrary keyed items by the topics they reference.

    Each item is `(key, topic_ids)`. Returns `(key, score, reason)` ordered
    highest first, where the score is the item's strongest topic signal and the
    reason is that topic's sentence -- so a caller can say "moved up because X"
    and mean it.

    Stability matters more here than in `rank_topics`: this reorders a list a
    model already put in a deliberate sequence, so items that tie keep their
    original relative order (Python's sort is stable) and items referencing no
    ranked topic score 0 and stay put behind the ones that do.
    """
    by_topic = {s.topic_id: s for s in signals}
    scored: list[tuple[str, float, str | None]] = []
    for key, topic_ids in items:
        matched = [by_topic[tid] for tid in topic_ids if tid in by_topic]
        if matched:
            best = max(matched, key=lambda s: s.score)
            scored.append((key, best.score, best.reason))
        else:
            scored.append((key, 0.0, None))
    scored.sort(key=lambda row: -row[1])
    return scored
