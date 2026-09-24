"""JSON-out Hermes calls for quiz generation.

Same contract `app.goals.llm` and the tagger already established: reply with
JSON only, strip the fence the model adds anyway, and treat an unparseable
answer as a failure the caller can recover from rather than an exception that
kills a graph run mid-interrupt.

Nodes are sync for the same reason `app.goals.llm`'s are: LangGraph's
SqliteSaver is sync and the router runs the whole graph in a worker thread, so
this bridges to the async Hermes client with `anyio.from_thread` where it can,
and its own event loop where there is no surrounding one (tests, scripts).
"""

import json
import logging
import re
from typing import Any

import anyio
import anyio.from_thread

from agent import hermes

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)

SYSTEM_PROMPT = (
    "You write quiz questions from a student's own study material. "
    "You reply with JSON only -- no prose, no code fences."
)


class LLMUnavailable(RuntimeError):
    """Hermes could not be reached or did not answer in the agreed shape."""


def _run(factory):
    try:
        return anyio.from_thread.run(factory)
    except hermes.HermesError:
        raise
    except RuntimeError:
        return anyio.run(factory)


def _parse_json(raw: str) -> Any:
    text = _FENCE.sub("", raw or "").strip()
    if not text:
        raise LLMUnavailable("Hermes returned an empty response")
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                pass
        log.warning("quiz create: unparseable Hermes response: %s", text[:300])
        raise LLMUnavailable("Hermes did not return JSON")


def ask_json(prompt: str, *, system: str = SYSTEM_PROMPT) -> Any:
    """One Hermes round-trip that must come back as JSON."""
    try:
        raw = _run(lambda: hermes.complete(prompt, system=system))
    except hermes.HermesError as exc:
        raise LLMUnavailable(str(exc)) from exc
    return _parse_json(raw)
