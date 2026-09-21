"""`personalize_decomposition` -- step 2b, and the spec's branching rule.

Per milestone: try to ground it in uploaded Materials; if nothing correlates,
take the research branch instead of forcing a link. Then reorder the whole list
against weak-topic signal using `app.ranking` -- the same function Step 7's
Dashboard Priority Feed calls, not a second ranker.

Everything user-visible that this node writes has to be traceable to a signal it
actually read. That is the feature: the student can see why a milestone moved.
"""

import logging
from typing import Any

from app.goals import research
from app.goals.llm import LLMUnavailable, ask_json
from app.goals.state import Milestone, RoadmapState, format_effort
from app.materials.search import search_materials
from app.ranking import TopicSignal, rank_keyed, rank_topics

log = logging.getLogger(__name__)

# Lance returns L2 distance, so lower is closer. Above this a "hit" is really
# just the nearest thing in a small corpus and grounding on it would be a lie.
GROUNDING_MAX_DISTANCE = 1.1
# Chunks to keep as provenance per milestone -- enough for `N chunks tagged
# "Topic"` to mean something, few enough to stay a readable list.
PROVENANCE_LIMIT = 5


def _ground(milestone: Milestone) -> tuple[list[int], list[int], str | None]:
    """Search Materials for one milestone.

    Returns (topic_ids, chunk_ids, topic_name). Empty topic_ids means nothing
    correlated and the caller should take the research branch.
    """
    query = f"{milestone['title']} {milestone.get('description', '')}".strip()
    try:
        found = search_materials(None, query, limit=PROVENANCE_LIMIT)
    except Exception as exc:  # LanceDB missing, empty table, model not warmed
        log.warning("roadmap: materials search failed for %r (%s)", query, exc)
        return [], [], None

    hits = [
        hit
        for hit in found.get("results", [])
        if hit.get("topic_id") is not None
        and (hit.get("distance") is None or hit["distance"] <= GROUNDING_MAX_DISTANCE)
    ]
    if not hits:
        return [], [], None

    # Topic ids in hit order, deduped: the first is the best match and becomes
    # the milestone's subject for copy purposes.
    topic_ids: list[int] = []
    for hit in hits:
        if hit["topic_id"] not in topic_ids:
            topic_ids.append(hit["topic_id"])
    return topic_ids, [hit["chunk_id"] for hit in hits], hits[0].get("topic_name")


def _copy_prompt(milestone: Milestone, evidence: str) -> str:
    return f"""Milestone: "{milestone['title']}"
What it involves: {milestone.get('description') or '(no description)'}

What the data says about this student and this milestone:
{evidence}

Write the two explanations the UI shows. The short one is a single line under the
title; the long one is what appears when the student expands the row.

Reply with JSON only:
{{"reason": "one line, under 110 chars, no trailing period",
  "reason_long": "2-3 sentences addressed to the student",
  "est_effort_min": <whole minutes>, "est_effort_max": <whole minutes>}}

Use only the facts above. Do not invent scores, dates or check-in counts. If the
facts are thin, say less."""


def _evidence(
    milestone: Milestone,
    signal: TopicSignal | None,
    chunk_count: int,
    ranking_reason: str | None,
) -> str:
    lines: list[str] = []
    if signal:
        lines.append(
            f"- Topic: {signal.topic_name} (understanding "
            f"{'no signal yet' if signal.understanding < 0 else f'{signal.understanding}/100'},"
            f" band {signal.band})"
        )
    if chunk_count:
        lines.append(f"- Uploaded material: {chunk_count} chunks matched this milestone")
    else:
        lines.append("- Uploaded material: nothing matched this milestone")
    if ranking_reason:
        lines.append(f"- Why it sits here: {ranking_reason}")
    return "\n".join(lines)


def personalize_decomposition(state: RoadmapState) -> dict[str, Any]:
    drafts: list[Milestone] = [dict(m) for m in (state.get("draft_milestones") or [])]  # type: ignore[misc]
    if not drafts:
        return {"draft_milestones": [], "status": "awaiting_approval"}

    topics = (state.get("materials_context") or {}).get("topics", [])
    signals = rank_topics(topics)
    by_topic = {s.topic_id: s for s in signals}

    # 1-3: ground each milestone, or branch to research.
    grounded_count = 0
    for milestone in drafts:
        topic_ids, chunk_ids, _ = _ground(milestone)
        if topic_ids:
            milestone["source"] = "materials"
            milestone["related_topic_ids"] = topic_ids
            milestone["source_chunk_ids"] = chunk_ids
            grounded_count += 1
        else:
            # Spec-mandated branch. Tagged honestly even though the tool is a
            # stub, so the eventual fetcher only has to fill in the body.
            note = research.investigate(milestone["title"], milestone.get("description", ""))
            milestone["source"] = "research"
            milestone["related_topic_ids"] = []
            milestone["source_chunk_ids"] = []
            milestone["reason"] = note.reason
            milestone["reason_long"] = note.reason_long

    # 4: reorder. Ungrounded milestones score 0 and keep the model's ordering
    # behind the ones that carry signal.
    ordered = rank_keyed([(m["id"], m.get("related_topic_ids", [])) for m in drafts], signals)
    by_id = {m["id"]: m for m in drafts}
    ranking_reasons = {key: reason for key, _, reason in ordered}
    drafts = [by_id[key] for key, _, _ in ordered]
    for position, milestone in enumerate(drafts, start=1):
        milestone["order"] = position

    # 5: the copy, once the order is final -- one call per milestone covering
    # both reasons and the effort range, so short and long cannot contradict
    # each other.
    for milestone in drafts:
        topic_ids = milestone.get("related_topic_ids") or []
        signal = next((by_topic[t] for t in topic_ids if t in by_topic), None)
        evidence = _evidence(
            milestone,
            signal,
            len(milestone.get("source_chunk_ids") or []),
            ranking_reasons.get(milestone["id"]),
        )
        try:
            copy = ask_json(_copy_prompt(milestone, evidence))
        except LLMUnavailable as exc:
            # Fall back to the ranker's own sentence. It is less fluent but it is
            # the true reason, which matters more than the prose.
            log.warning("roadmap: copy generation failed for %s (%s)", milestone["id"], exc)
            if not milestone.get("reason"):
                milestone["reason"] = ranking_reasons.get(milestone["id"]) or ""
            continue

        if copy.get("reason"):
            milestone["reason"] = str(copy["reason"]).strip()[:160]
        if copy.get("reason_long"):
            milestone["reason_long"] = str(copy["reason_long"]).strip()
        lo, hi = copy.get("est_effort_min"), copy.get("est_effort_max")
        if isinstance(lo, int) or isinstance(hi, int):
            effort = format_effort(
                lo if isinstance(lo, int) else None, hi if isinstance(hi, int) else None
            )
            if effort:
                milestone["est_effort"] = effort

    # `unlocks_after` comes from the ranking, not the model: a model asked to
    # invent prerequisites invents cycles. A milestone is gated on its immediate
    # predecessor only when they share a topic, which is when "you need that
    # first" is actually true.
    for previous, milestone in zip(drafts, drafts[1:]):
        shared = set(previous.get("related_topic_ids") or []) & set(
            milestone.get("related_topic_ids") or []
        )
        if shared:
            milestone["unlocks_after"] = previous["id"]

    if grounded_count == len(drafts):
        source = "materials"
    elif grounded_count == 0:
        source = "research"
    else:
        source = "mixed"

    return {
        "draft_milestones": drafts,
        "decomposition_source": source,
        "status": "awaiting_approval",
    }
