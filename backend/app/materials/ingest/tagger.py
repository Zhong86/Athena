"""Classify each chunk into exactly one topic.

MVP rule from the spec: one chunk = one topic. A chunk that cannot be placed is
left unassigned rather than forced somewhere wrong -- it stays embedded and
searchable, it just does not appear on a topic page.

No database access here. The tagger proposes topics; the pipeline creates them.
Proposed topics carry *negative* placeholder refs, which is what makes
cross-batch dedupe work: a topic invented in batch 1 is shown to the model in
batch 3 with its placeholder id, so "Entropy" is proposed once and reused
thereafter instead of arriving three times and needing a merge afterwards.
"""

import difflib
import json
import re
from dataclasses import dataclass

from agent import hermes

# Each batch is one Hermes call. 20 keeps the prompt well inside context while
# holding the call count down -- this loop is the slowest part of an ingest.
BATCH_SIZE = 20
# The opening of a chunk is enough to classify it; sending all 1000 chars would
# triple the prompt for no gain in accuracy.
CHUNK_PREVIEW_CHARS = 400
# Without a ceiling the model shreds one PDF into dozens of near-duplicate
# topics and the Materials page becomes unusable.
MAX_NEW_TOPICS = 8
# Similarity required to fold an over-cap proposal into an existing topic.
_SIMILARITY_CUTOFF = 0.6

_FENCE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)

SYSTEM_PROMPT = (
    "You classify excerpts of study material into topics. "
    "You reply with JSON only -- no prose, no code fences."
)


@dataclass(frozen=True)
class ProposedTopic:
    ref: int  # negative placeholder; the pipeline swaps it for a real row id
    name: str
    description: str | None


@dataclass(frozen=True)
class TaggingResult:
    # One entry per input chunk: a positive existing topic id, a negative
    # placeholder from `proposed`, or None when the chunk could not be placed.
    refs: list[int | None]
    proposed: list[ProposedTopic]


def _prompt(texts: list[str], known: list[dict]) -> str:
    catalogue = (
        "\n".join(
            f"  {t['id']}: {t['name']}"
            + (f" — {t['description']}" if t.get("description") else "")
            for t in known
        )
        or "  (none yet)"
    )
    excerpts = "\n\n".join(
        f"[{i}] {text[:CHUNK_PREVIEW_CHARS]}" for i, text in enumerate(texts)
    )
    return f"""Existing topics:
{catalogue}

Excerpts:
{excerpts}

Assign every excerpt to exactly one topic.
- Prefer an existing topic: reply with its numeric id as "topic_id".
- Only if none genuinely fits, invent one: reply with "topic_name" and a one-sentence "topic_description".
- Topic names are short subject areas ("Entropy", "Ionic bonding"), not titles of the excerpt.

Reply with JSON of exactly this shape, one entry per excerpt:
{{"assignments": [{{"index": 0, "topic_id": 3}}, {{"index": 1, "topic_name": "...", "topic_description": "..."}}]}}"""


def _parse(raw: str) -> list[dict]:
    """Tolerate fences and stray prose around the JSON.

    A malformed reply costs this batch its topics, never the upload: the caller
    turns an empty list into None refs.
    """
    text = _FENCE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []

    assignments = payload.get("assignments") if isinstance(payload, dict) else None
    return [a for a in assignments if isinstance(a, dict)] if isinstance(assignments, list) else []


class _Resolver:
    """Maps whatever the model said onto a topic ref, carrying proposed topics
    across batches so the same invented name is only created once."""

    def __init__(self, existing: list[dict]):
        self._known: list[dict] = [dict(t) for t in existing]
        self._by_name = {t["name"].strip().lower(): t["id"] for t in self._known}
        self._valid_ids = {t["id"] for t in self._known}
        self.proposed: list[ProposedTopic] = []

    @property
    def known(self) -> list[dict]:
        return self._known

    def resolve(self, assignment: dict) -> int | None:
        topic_id = assignment.get("topic_id")
        if isinstance(topic_id, int) and topic_id in self._valid_ids:
            return topic_id

        name = assignment.get("topic_name")
        if not isinstance(name, str) or not name.strip():
            # Hallucinated id, or neither field present.
            return None

        name = name.strip()
        key = name.lower()
        if key in self._by_name:
            return self._by_name[key]

        if len(self.proposed) >= MAX_NEW_TOPICS:
            return self._nearest_existing(name)

        ref = -(len(self.proposed) + 1)
        description = assignment.get("topic_description")
        proposal = ProposedTopic(
            ref=ref,
            name=name,
            description=description if isinstance(description, str) else None,
        )
        self.proposed.append(proposal)
        self._by_name[key] = ref
        self._valid_ids.add(ref)
        self._known.append(
            {"id": ref, "name": proposal.name, "description": proposal.description}
        )
        return ref

    def _nearest_existing(self, name: str) -> int | None:
        names = list(self._by_name)
        match = difflib.get_close_matches(
            name.lower(), names, n=1, cutoff=_SIMILARITY_CUTOFF
        )
        return self._by_name[match[0]] if match else None


async def assign_topics(
    texts: list[str], existing_topics: list[dict]
) -> TaggingResult:
    """Batches run sequentially: Hermes is one local gateway, so concurrent
    calls would queue there anyway, and sequential order is what lets batch N
    see the topics batch N-1 invented."""
    refs: list[int | None] = []
    resolver = _Resolver(existing_topics)

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        try:
            raw = await hermes.complete(
                _prompt(batch, resolver.known), system=SYSTEM_PROMPT
            )
            assignments = _parse(raw)
        except hermes.HermesError:
            # The agent being down must not lose the upload -- the chunks are
            # already stored and can be re-tagged by retrying the ingest.
            assignments = []

        by_index: dict[int, int | None] = {}
        for a in assignments:
            index = a.get("index")
            if isinstance(index, int) and 0 <= index < len(batch):
                by_index[index] = resolver.resolve(a)

        refs.extend(by_index.get(i) for i in range(len(batch)))

    return TaggingResult(refs=refs, proposed=resolver.proposed)
