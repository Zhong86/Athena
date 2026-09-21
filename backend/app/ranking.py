"""Weak topic x near deadline ranking -- one implementation, two callers.

Step 6's `personalize_decomposition` orders roadmap milestones with this, and
Step 7's Dashboard Priority Feed ranks topics with it. The implementation plan is
explicit that these must be the same function rather than two rankers that agree
by coincidence, so the shared part lives at `app/` level and neither feature owns
it.

Every ranked result carries the sentence that explains it. A score with no
traceable reason is the failure mode to avoid here: the whole point of the
feature is that the user can see *why* something moved up.

Band thresholds match `frontend/src/lib/api.ts:understandingBand` (40 / 70). They
are duplicated rather than derived because the backend needs them to generate
copy and the frontend needs them to pick a colour; if they drift, the number on
screen stops matching the word next to it.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

WEAK_BELOW = 40
FAIR_BELOW = 70
# No signal yet. Emphatically *not* the same as weak: a fresh install would
# otherwise produce a roadmap claiming evidence it does not have.
NO_SIGNAL = -1

# A deadline further out than this exerts no pressure on ordering.
DEADLINE_HORIZON_DAYS = 21
# How much a topic's name has to resemble an event title to be considered the
# subject of that deadline. Same cutoff as the tagger's topic dedupe.
_TITLE_SIMILARITY = 0.6


def band(score: int) -> str:
    """"unknown" | "weak" | "fair" | "strong" -- mirrors the frontend's bands."""
    if score < 0:
        return "unknown"
    if score < WEAK_BELOW:
        return "weak"
    if score < FAIR_BELOW:
        return "fair"
    return "strong"


@dataclass(frozen=True)
class Deadline:
    title: str
    due_at: str
    days_until: int


@dataclass
class TopicSignal:
    """One topic's priority, plus the evidence that produced it."""

    topic_id: int
    topic_name: str
    understanding: int
    score: float
    reason: str
    deadline: Deadline | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def band(self) -> str:
        return band(self.understanding)


def _urgency(days_until: int | None) -> float:
    """1.0 for something due today, decaying to 0 at the horizon.

    Overdue counts as maximally urgent rather than negative -- a missed deadline
    is the most useful thing to surface, not the least.
    """
    if days_until is None:
        return 0.0
    if days_until <= 0:
        return 1.0
    if days_until >= DEADLINE_HORIZON_DAYS:
        return 0.0
    return 1.0 - (days_until / DEADLINE_HORIZON_DAYS)


def _need(understanding: int) -> float:
    """How much work a topic needs. Unknown sits between weak and fair.

    An unscored topic is worth surfacing (we should find out) but must not
    outrank a topic measured as weak (we know).
    """
    if understanding == NO_SIGNAL:
        return 0.55
    return 1.0 - (max(0, min(100, understanding)) / 100)


def match_deadline(topic_name: str, deadlines: Sequence[Deadline]) -> Deadline | None:
    """The nearest deadline whose title mentions this topic.

    Substring first, fuzzy second: "Entropy" should match "Entropy problem set"
    outright, and "Heat transfer lab" should still match "heat-transfer lab".
    Unmatched is the common case and returns None -- a topic with no named
    deadline is ranked on need alone rather than borrowing someone else's
    urgency.
    """
    name = topic_name.strip().lower()
    if not name:
        return None

    candidates = [d for d in deadlines if name in d.title.lower()]
    if not candidates:
        span = len(name.split())
        for deadline in deadlines:
            words = deadline.title.lower().split()
            # Compare against same-length word windows, not the whole title:
            # "Entropy" vs "Week 4 entropy problem set" scores far below cutoff
            # as a whole-string ratio even though the match is exact.
            windows = [" ".join(words[i : i + span]) for i in range(len(words))]
            if any(
                difflib.SequenceMatcher(None, name, w).ratio() >= _TITLE_SIMILARITY
                for w in windows
            ):
                candidates.append(deadline)
    if not candidates:
        return None
    return min(candidates, key=lambda d: d.days_until)


def _reason(name: str, understanding: int, deadline: Deadline | None) -> str:
    """The sentence shown next to the ranking. Only states what was measured."""
    tone = band(understanding)
    if tone == "unknown":
        head = f"{name} has no check-in signal yet"
    elif tone == "weak":
        head = f"{name} is scoring weak ({understanding}/100)"
    elif tone == "fair":
        head = f"{name} is still building ({understanding}/100)"
    else:
        head = f"{name} is solid ({understanding}/100)"

    if deadline is None:
        return head
    if deadline.days_until < 0:
        return f"{head} · {deadline.title} was due {abs(deadline.days_until)}d ago"
    if deadline.days_until == 0:
        return f"{head} · {deadline.title} is due today"
    return f"{head} · {deadline.title} due in {deadline.days_until} days"


def rank_topics(
    topics: Iterable[dict[str, Any]], deadlines: Sequence[Deadline] = ()
) -> list[TopicSignal]:
    """Topics, most worth attention first. Dashboard's Priority Feed calls this.

    `topics` rows need `id`, `name`, `user_understanding`.
    """
    signals: list[TopicSignal] = []
    for topic in topics:
        understanding = int(topic.get("user_understanding", NO_SIGNAL))
        deadline = match_deadline(topic["name"], deadlines)
        need = _need(understanding)
        urgency = _urgency(deadline.days_until if deadline else None)
        # Urgency amplifies need rather than replacing it: a strong topic with a
        # deadline tomorrow still should not outrank a weak topic with one.
        score = need * (1.0 + urgency)
        signals.append(
            TopicSignal(
                topic_id=int(topic["id"]),
                topic_name=topic["name"],
                understanding=understanding,
                score=round(score, 4),
                reason=_reason(topic["name"], understanding, deadline),
                deadline=deadline,
                evidence={"need": round(need, 4), "urgency": round(urgency, 4)},
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
