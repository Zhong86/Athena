"""JSON-out Hermes calls for the roadmap nodes.

Same contract the tagger already established: reply with JSON only, strip the
fence the model adds anyway, and treat an unparseable answer as a failure the
caller can recover from rather than an exception that kills a graph run
mid-interrupt.

Nodes are sync because LangGraph's SqliteSaver is sync and the router already
runs the whole graph in a worker thread -- so this bridges to the async Hermes
client with `anyio.from_thread` where it can, and its own event loop where there
is no surrounding one (tests, scripts).
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
    "You plan study roadmaps for a single student. "
    "You reply with JSON only -- no prose, no code fences."
)


class LLMUnavailable(RuntimeError):
    """Hermes could not be reached or did not answer in the agreed shape."""


def _run(factory):
    """Call an async Hermes function from sync node code.

    Two paths on purpose: inside the router the node already runs in a worker
    thread belonging to a live event loop, so work is handed back to it; in a
    test or a script there is no loop to hand to, so one is started.

    `factory` is a callable, not a coroutine, because the fallback path needs a
    *fresh* coroutine -- an already-awaited one cannot be re-run. And
    `HermesError` is re-raised before the RuntimeError branch because it
    subclasses RuntimeError: a gateway failure must not be mistaken for "no
    event loop here" and retried.
    """
    try:
        return anyio.from_thread.run(factory)
    except hermes.HermesError:
        raise
    except RuntimeError:
        return anyio.run(factory)


def ask_json(prompt: str, *, system: str = SYSTEM_PROMPT) -> Any:
    """One Hermes round-trip that must come back as JSON."""
    try:
        raw = _run(lambda: hermes.complete(prompt, system=system))
    except hermes.HermesError as exc:
        raise LLMUnavailable(str(exc)) from exc

    text = _FENCE.sub("", raw or "").strip()
    if not text:
        raise LLMUnavailable("Hermes returned an empty response")
    try:
        return json.loads(text)
    except ValueError:
        # A model that narrates before its JSON is common enough to be worth one
        # salvage attempt; anything less structured than that is a real failure.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                pass
        log.warning("roadmap: unparseable Hermes response: %s", text[:300])
        raise LLMUnavailable("Hermes did not return JSON")
