"""`clarify_intent` -- step 1 of the spec's flow.

"Want to pass this test" is not yet a goal: no timeframe, no scope, nothing to
decompose. This node asks until it is, bounded by a max-turns guard, and extracts
the goal-level fields (course code, due date, category) that `commit_roadmap`
later persists.
"""

import logging
from typing import Any

from langgraph.types import interrupt

from app.goals.llm import LLMUnavailable, ask_json
from app.goals.state import MAX_CLARIFY_TURNS, RoadmapState

log = logging.getLogger(__name__)


def _prompt(raw_goal: str, turns: list[dict], known_topics: list[str]) -> str:
    transcript = (
        "\n".join(f"Q: {t['question']}\nA: {t['answer']}" for t in turns)
        or "  (nothing asked yet)"
    )
    topics_line = (
        f"The student has uploaded material on: {', '.join(known_topics)}."
        if known_topics
        else "The student has not uploaded any material yet."
    )
    return f"""A student stated this goal:
"{raw_goal}"

{topics_line}

Clarification so far:
{transcript}

Decide whether this goal is specific enough to break into study milestones. It is
specific enough when you can tell *what* is being prepared for and *by when*.

Reply with JSON:
{{
  "needs_clarification": true|false,
  "questions": ["at most two questions, each answerable in one line"],
  "suggested_answers": [["2-4 short likely answers for question 1"], ["...for question 2"]],
  "clarified_goal": "one sentence restating the goal precisely, or null",
  "extracted": {{
    "short_name": "3-4 word label for navigation, e.g. Thermo midterm",
    "course_code": "the course/class name or code if known, else null",
    "category": "academic" | "career",
    "due_at": "YYYY-MM-DD or null",
    "derivation": "one clause on what this was derived from, or null"
  }}
}}

Ask only what changes the roadmap. Never ask for something the student already
said. When a question is about which subject or topic, prefer the student's
uploaded topics above as suggested answers over a generic guess -- but only
where one plausibly fits; do not force-fit an unrelated topic onto the goal. If
two rounds of questions have not settled it, set needs_clarification to false
and commit to your best reading."""


def _fallback_goal(state: RoadmapState, turns: list[dict]) -> str:
    """Used when Hermes is unreachable or the turn guard is spent.

    Concatenating the transcript is a poor goal statement, but it is the
    student's own words -- which beats blocking the run or inventing a goal they
    never stated.
    """
    answers = " ".join(t["answer"] for t in turns if t.get("answer"))
    return f"{state['raw_goal_input']} {answers}".strip()


def clarify_intent(state: RoadmapState) -> dict[str, Any]:
    """Two passes per round, and the interrupt is always the *first* thing.

    A resumed node re-executes from its first line, so collecting the answer has
    to happen before anything else -- ask Hermes first and it decides the goal is
    clear, returns early, and the student's reply never reaches the transcript.
    So: one pass generates questions and parks them in state, the conditional
    edge loops back here, and the next pass opens by interrupting to collect them.
    """
    turns = list(state.get("clarification_turns") or [])
    pending = list(state.get("clarifying_questions") or [])

    if pending:
        answer = interrupt(
            {
                "kind": "clarify",
                "questions": pending,
                "suggested_answers": state.get("suggested_answers") or [],
                # Sent every time so a reloaded page can redraw the whole thread
                # without holding it client-side across requests.
                "clarification_turns": turns,
            }
        )
        for question, reply_text in zip(pending, _answers(answer, len(pending))):
            turns.append({"question": question, "answer": reply_text})
        pending = []

    # Spec-mandated guard. Checked before the call, not after: a fourth round of
    # questions must not even be generated.
    if len(turns) >= MAX_CLARIFY_TURNS:
        log.info("roadmap: clarify turn guard hit after %d turns", len(turns))
        return {
            "clarified_goal": _fallback_goal(state, turns),
            "clarification_turns": turns,
            "clarifying_questions": [],
            "status": "decomposing",
        }

    try:
        reply = ask_json(_prompt(state["raw_goal_input"], turns, state.get("known_topics") or []))
    except LLMUnavailable as exc:
        # Proceed on the student's own words rather than stranding the run. A
        # dead gateway will surface at decomposition, which cannot degrade.
        log.warning("roadmap: clarify unavailable (%s), proceeding unclarified", exc)
        return {
            "clarified_goal": _fallback_goal(state, turns),
            "clarification_turns": turns,
            "clarifying_questions": [],
            "status": "decomposing",
        }

    questions = [q for q in (reply.get("questions") or []) if str(q).strip()][:2]
    extracted = reply.get("extracted") or {}

    if not reply.get("needs_clarification") or not questions:
        return {
            "clarified_goal": (reply.get("clarified_goal") or _fallback_goal(state, turns)),
            "clarification_turns": turns,
            "clarifying_questions": [],
            "goal_fields": extracted,
            "status": "decomposing",
        }

    # Park the questions and loop. The quick-reply chips come from the model;
    # without them the approval UI's `.clarify-option` markup has nothing to
    # render, so they are parked alongside the questions they belong to.
    suggested = reply.get("suggested_answers") or []
    return {
        "clarifying_questions": questions,
        "suggested_answers": suggested[: len(questions)],
        "clarification_turns": turns,
        "goal_fields": extracted,
        "status": "clarifying",
    }


def _answers(payload: Any, count: int) -> list[str]:
    """Resume payloads arrive as `{"answers": [...]}`, a bare list, or a single
    string -- the mockup lets the student type freely or tap a chip."""
    if isinstance(payload, dict):
        payload = payload.get("answers", payload.get("answer", ""))
    if isinstance(payload, str):
        answers = [payload]
    elif isinstance(payload, list):
        answers = [str(a) for a in payload]
    else:
        answers = [str(payload)]
    # One answer to two questions is normal in a chat thread; pad so the
    # transcript keeps its question/answer pairing rather than dropping a turn.
    return (answers + [""] * count)[:count]
