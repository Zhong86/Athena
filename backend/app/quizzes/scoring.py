"""Turning a graded quiz into a topic's understanding score, with its evidence.

The implementation plan's done-condition for this step is that a quiz round-trip
moves a topic's score *and the cause stays queryable*. So every function here
returns the reason alongside the number, and the repository writes both in one
transaction.

Two rules do most of the work:

Only a fully graded quiz moves the score. If the open-ended pass failed halfway,
the remaining evidence is the multiple-choice half -- which is systematically
easier -- and blending that in would quietly inflate the topic. A partial grade
is reported as a grading error and changes nothing.

A score is a blend, not a replacement. One quiz is a sample, and a single bad
morning should not erase a month of evidence; but the newest measurement is also
the most relevant, so it carries the larger share.
"""

from typing import Any

from app.ranking import NO_SIGNAL, band

# How much of the new understanding comes from this quiz when the topic already
# has a score. 0.6 leans on the newest evidence while keeping the history
# visible; at 1.0 the score would be "the last quiz" rather than "understanding".
QUIZ_WEIGHT = 0.6


def quiz_score(grades: list[int]) -> int:
    """The quiz's own 0..100, before any blending.

    A plain mean: multiple choice already scores 100/0 on the same scale as the
    open-ended grades, which is what lets one average cover both kinds without
    weighting one against the other.
    """
    if not grades:
        return 0
    return int(round(sum(grades) / len(grades)))


def blend(previous: int, score: int) -> int:
    """The topic's new understanding.

    A first signal is taken at face value -- there is nothing to blend with, and
    averaging against the -1 sentinel would be arithmetic on a flag.
    """
    if previous == NO_SIGNAL:
        return score
    previous = max(0, min(100, previous))
    return int(round(previous * (1 - QUIZ_WEIGHT) + score * QUIZ_WEIGHT))


def reason(
    topic_name: str, previous: int, understanding: int, score: int, graded: list[dict[str, Any]]
) -> str:
    """The sentence shown next to the score. States only what was measured."""
    correct = sum(1 for g in graded if g["correct"])
    head = f"Scored {score}/100 on “{topic_name}” ({correct}/{len(graded)} correct)"

    if previous == NO_SIGNAL:
        return f"{head} — first check-in, so this sets the score to {understanding}."
    if understanding > previous:
        return f"{head} — understanding up from {previous} to {understanding} ({band(understanding)})."
    if understanding < previous:
        return f"{head} — understanding down from {previous} to {understanding} ({band(understanding)})."
    return f"{head} — understanding holds at {understanding} ({band(understanding)})."


def evidence(score: int, previous: int, graded: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers behind `reason`, so the sentence can be checked, not trusted.

    Per-question rows are included because "why this score" has to survive the
    quiz being deleted -- at which point the attempt rows are gone too.
    """
    return {
        "quiz_score": score,
        "previous_understanding": previous,
        "quiz_weight": QUIZ_WEIGHT,
        "questions": [
            {
                "question_id": g["question_id"],
                "kind": g["kind"],
                "score": g["score"],
                "correct": g["correct"],
                "graded_by": g["graded_by"],
            }
            for g in graded
        ],
    }
