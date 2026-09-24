"""`generate_questions` -- step 3: material -> grounded questions with a key.

Every question is required to cite the excerpts it was written from
(`source_excerpts`), which become the question's `resources`. That is what
lets `app.quizzes.grading` grade an open-ended answer against the same
material the question came from later -- its module docstring is explicit
that grading against a fresh search instead would produce "a defensible-
sounding score the student cannot argue with," which is the thing to avoid.

Chunks come from `chunks_for_topic` directly, not `search_materials`: the
topic was already chosen by the student, so there is no query to embed --
every chunk tagged with that topic is in scope, the same corpus the topic
page itself shows.
"""

import logging
from typing import Any

from app.db import connection
from app.materials import repository as materials_repo
from app.quizzes.llm import LLMUnavailable, ask_json
from app.quizzes.state import NoMaterials, QuestionDraft, QuizDraftState

log = logging.getLogger(__name__)

# Enough chunks for a topic's real breadth without blowing the prompt budget --
# the tagger's batches cap at 20 for the same reason.
MATERIAL_CHUNK_LIMIT = 40
CHUNK_PREVIEW_CHARS = 500
MIN_OPTIONS = 2

_KIND_RULE = {
    "multiple_choice": 'Every question must be "multiple_choice".',
    "open_ended": 'Every question must be "open_ended".',
    "mixed": 'Write a mix of "multiple_choice" and "open_ended" questions.',
}


def _prompt(topic_name: str, fmt: str, count: int, chunks: list[dict]) -> str:
    excerpts = "\n\n".join(
        f"[{i}] {c['text'][:CHUNK_PREVIEW_CHARS]}" for i, c in enumerate(chunks)
    )
    return f"""Study material on "{topic_name}":
{excerpts}

Write exactly {count} quiz questions testing understanding of this material.
{_KIND_RULE[fmt]}

A multiple_choice question needs 4 options and exactly one correct answer. An
open_ended question needs a rubric describing what a full-credit answer covers.

Reply with JSON only:
{{"title": "short quiz title",
  "questions": [
    {{"kind": "multiple_choice" | "open_ended",
      "prompt": "...",
      "options": ["...", "...", "...", "..."],
      "correct_option": 0,
      "rubric": "what a full-credit answer covers, or null for multiple_choice",
      "explanation": "why that answer is correct -- shown to the student after grading",
      "source_excerpts": [0, 2]}}
  ]}}

"options" and "correct_option" are only for multiple_choice -- use [] and null for
open_ended. Ground every question in the excerpts above; do not test anything they
do not cover. "source_excerpts" names the excerpt numbers a question draws from --
never invent a number outside the list."""


def _question(item: dict, fmt: str, chunk_ids: list[int]) -> QuestionDraft | None:
    # A format of "mixed" trusts the model's own kind; anything else was told
    # what every question must be, so a stray mismatch is coerced rather than
    # dropped -- the same tolerance `decompose_goal` gives a model reply.
    kind = item.get("kind") if fmt == "mixed" else fmt
    if kind not in ("multiple_choice", "open_ended"):
        return None

    prompt = str(item.get("prompt") or "").strip()
    if not prompt:
        return None

    cited = [
        i for i in (item.get("source_excerpts") or []) if isinstance(i, int) and 0 <= i < len(chunk_ids)
    ]
    resources = [{"kind": "chunk", "chunk_id": chunk_ids[i]} for i in dict.fromkeys(cited)]
    explanation = str(item.get("explanation") or "").strip() or None

    if kind == "multiple_choice":
        options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
        correct = item.get("correct_option")
        if len(options) < MIN_OPTIONS or not isinstance(correct, int) or not 0 <= correct < len(options):
            return None
        return {
            "kind": "multiple_choice",
            "prompt": prompt[:4000],
            "options": options,
            "correct_option": correct,
            "rubric": None,
            "explanation": explanation,
            "resources": resources,
        }

    return {
        "kind": "open_ended",
        "prompt": prompt[:4000],
        "options": [],
        "correct_option": None,
        "rubric": str(item.get("rubric") or "").strip() or None,
        "explanation": explanation,
        "resources": resources,
    }


def generate_questions(state: QuizDraftState) -> dict[str, Any]:
    topic_id = state["topic_id"]
    with connection() as conn:
        chunks = materials_repo.chunks_for_topic(conn, topic_id, limit=MATERIAL_CHUNK_LIMIT)

    if not chunks:
        # choose_topic only offers topics with material, but a topic can be
        # emptied (chunks re-tagged elsewhere) in the gap between that read
        # and this one.
        raise NoMaterials(f"No material left for topic {state.get('topic_name')!r}")

    fmt = state["question_format"]
    count = state["question_count"]
    chunk_ids = [c["id"] for c in chunks]

    try:
        reply = ask_json(_prompt(state["topic_name"], fmt, count, chunks))
    except LLMUnavailable:
        # Nothing to fall back on: a quiz is the product, same reasoning as
        # app.goals.nodes.decompose_goal.
        log.error("quiz create: generation failed for topic %s", topic_id)
        raise

    questions: list[QuestionDraft] = []
    for item in (reply.get("questions") or [])[:count]:
        question = _question(item, fmt, chunk_ids)
        if question:
            questions.append(question)

    if not questions:
        raise LLMUnavailable("quiz generation produced no usable questions")

    used_chunk_ids: list[int] = []
    for question in questions:
        for resource in question["resources"]:
            if resource["chunk_id"] not in used_chunk_ids:
                used_chunk_ids.append(resource["chunk_id"])

    return {
        "draft_questions": questions,
        "quiz_title": str(reply.get("title") or "").strip()[:200] or f"{state['topic_name']} quiz",
        "resources": [{"kind": "chunk", "chunk_id": cid} for cid in used_chunk_ids],
        "review_error": None,
        "status": "reviewing",
    }
