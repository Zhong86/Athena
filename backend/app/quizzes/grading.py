"""Two graders, one score scale.

Multiple choice is settled by the key: 100 or 0, no model call, no ambiguity.
Open-ended goes to Hermes with the resources the quiz was *generated* from, and
comes back with a 0..100 score plus the sentences that justify it.

Grading against the generation resources rather than against a fresh search is
the whole point. A question written from chapter 4 and graded against chapter 9
produces a defensible-sounding score that the student cannot argue with, because
the material behind it was never the material the question came from. So the
resources travel with the quiz, and grading reads them back.

Chunk resources are rehydrated from SQLite at grading time -- they are pointers,
and an edited chunk should grade against its current wording. External resources
carry their own excerpt: the backend has no outbound web access, so a page that
moved since generation would otherwise silently drop out of the grade.

No database access in here. The router loads the rows and passes them in, which
is what lets the whole grading pass be unit-tested without a connection.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent import hermes
from app.ranking import FAIR_BELOW

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)

# An open-ended answer counts as `correct` (001's boolean, which the Sessions
# log and any existing query still read) once it scores at least "fair".
# Imported from ranking rather than picked fresh so the word next to the number
# means the same thing here as it does on the Materials page.
CORRECT_AT = FAIR_BELOW

# Long excerpts crowd the answer out of the prompt and make the model grade the
# material instead of the student.
RESOURCE_PREVIEW_CHARS = 700
# Beyond this the prompt stops being about the question.
MAX_RESOURCES = 8

SYSTEM_PROMPT = (
    "You grade a single student answer against supplied reference material. "
    "You reply with JSON only -- no prose, no code fences."
)


@dataclass
class Grade:
    score: int  # 0..100
    feedback: str | None
    graded_by: str  # 'key' | 'hermes'
    resources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return self.score >= CORRECT_AT


class GradingUnavailable(RuntimeError):
    """Hermes could not be reached, or did not answer in the agreed shape."""


# --------------------------------------------------------------------------
# multiple choice
# --------------------------------------------------------------------------


def grade_multiple_choice(question: dict[str, Any], selected_option: int | None) -> Grade:
    """100 or 0. An unanswered or out-of-range selection is simply wrong.

    Out-of-range is graded rather than rejected because the question may have
    been edited after the answer was saved; failing the submit over it would
    strand the whole quiz.
    """
    options: list[str] = question.get("options") or []
    correct_option = question.get("correct_option")

    if selected_option is None:
        return Grade(score=0, feedback="No answer given.", graded_by="key")
    if not 0 <= selected_option < len(options):
        return Grade(score=0, feedback="No valid option selected.", graded_by="key")

    if selected_option == correct_option:
        feedback = question.get("explanation") or "Correct."
        return Grade(score=100, feedback=feedback, graded_by="key")

    answer = options[correct_option] if correct_option is not None else "unknown"
    feedback = f"Incorrect — the answer is “{answer}”."
    if question.get("explanation"):
        feedback = f"{feedback} {question['explanation']}"
    return Grade(score=0, feedback=feedback, graded_by="key")


# --------------------------------------------------------------------------
# open ended
# --------------------------------------------------------------------------


def resolve_resources(
    question: dict[str, Any],
    quiz_resources: list[dict[str, Any]],
    chunk_texts: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The reference material for one question, in the order it gets numbered.

    A question's own resources win outright when it has any; only when it has
    none does it fall back to the quiz's. Merging the two would bury a
    deliberately narrow reference under the whole quiz's material, and
    narrowing is the only reason to set per-question resources at all.

    Chunk entries whose row is gone are dropped rather than rendered empty: a
    numbered blank in the prompt is something the model will try to interpret.
    """
    chosen = question.get("resources") or quiz_resources
    resolved: list[dict[str, Any]] = []
    for resource in chosen[:MAX_RESOURCES]:
        if resource.get("kind") == "chunk":
            row = chunk_texts.get(resource.get("chunk_id"))
            if row is None:
                continue
            resolved.append(
                {
                    "kind": "chunk",
                    "chunk_id": row["id"],
                    "title": row.get("source_filename") or row.get("topic_name"),
                    "text": row["text"],
                }
            )
        else:
            if not (resource.get("text") or "").strip():
                continue
            resolved.append(
                {
                    "kind": "external",
                    "url": resource.get("url"),
                    "title": resource.get("title") or resource.get("url"),
                    "text": resource["text"],
                }
            )
    return resolved


def _resource_block(resources: list[dict[str, Any]]) -> str:
    if not resources:
        return "  (none supplied — grade on the rubric alone)"
    lines = []
    for i, r in enumerate(resources):
        label = r.get("title") or (
            f"chunk {r['chunk_id']}" if r.get("kind") == "chunk" else "source"
        )
        origin = f" ({r['url']})" if r.get("kind") == "external" and r.get("url") else ""
        lines.append(f"[{i}] {label}{origin}\n{r['text'][:RESOURCE_PREVIEW_CHARS]}")
    return "\n\n".join(lines)


def _prompt(question: dict[str, Any], answer: str, resources: list[dict[str, Any]]) -> str:
    rubric = question.get("rubric") or (
        "(none given — judge the answer against the reference material)"
    )
    return f"""Question:
{question['prompt']}

What a full-credit answer covers:
{rubric}

The student's answer:
{answer.strip() or "(left blank)"}

Reference material this question was written from:
{_resource_block(resources)}

Grade the answer out of 100.
- Judge it against the rubric and the reference material, nothing else.
- A correct answer that goes beyond the material is not penalised for that.
- Where the material does not settle a point, say so in the feedback instead of guessing.
- "cited" lists the reference numbers you actually used; use [] if none applied.
- Address the feedback to the student, in one or two sentences.

Reply with JSON of exactly this shape:
{{"score": 0, "feedback": "...", "cited": [0]}}"""


def _parse(raw: str) -> dict[str, Any] | None:
    """Tolerate fences and stray prose around the JSON, as the tagger does."""
    text = _FENCE.sub("", raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_score(value: Any) -> int | None:
    """Accept 87, 87.0 and "87"; reject anything that is not a number.

    Clamping rather than rejecting an out-of-range number: a model that says 120
    means "full marks", and failing the whole grading pass over it would cost
    the user a quiz.
    """
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, score))


async def grade_open_ended(
    question: dict[str, Any], answer: str | None, resources: list[dict[str, Any]]
) -> Grade:
    """One Hermes round-trip per open-ended answer.

    A blank answer is still sent rather than shortcut to zero: the feedback the
    student gets should say what was missing, and only the grader knows that.
    """
    try:
        raw = await hermes.complete(
            _prompt(question, answer or "", resources), system=SYSTEM_PROMPT
        )
    except hermes.HermesError as exc:
        raise GradingUnavailable(str(exc)) from exc

    payload = _parse(raw)
    if payload is None:
        log.warning("quiz: unparseable grading response: %s", (raw or "")[:300])
        raise GradingUnavailable("Hermes did not return JSON")

    score = _coerce_score(payload.get("score"))
    if score is None:
        log.warning("quiz: grading response had no usable score: %s", str(payload)[:300])
        raise GradingUnavailable("Hermes did not return a score")

    feedback = payload.get("feedback")
    feedback = feedback.strip() if isinstance(feedback, str) else None

    # Only real indices survive. A hallucinated citation would otherwise show up
    # on the Materials page as evidence for a grade it never supported.
    cited = payload.get("cited")
    used = (
        [resources[i] for i in cited if isinstance(i, int) and 0 <= i < len(resources)]
        if isinstance(cited, list)
        else []
    )

    return Grade(score=score, feedback=feedback, graded_by="hermes", resources=used)
