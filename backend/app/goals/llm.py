"""JSON-out Hermes calls for the roadmap nodes.

Same contract the tagger already established: reply with JSON only, strip the
fence the model adds anyway, and treat an unparseable answer as a failure the
caller can recover from rather than an exception that kills a graph run
mid-interrupt. Two ways to ask: `ask_json` for a bare completion, `ask_agent_json`
for a Runs-API call that lets the gateway use its own tools -- see
`app.goals.research`, the one caller that needs the latter.

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


def _parse_json(raw: str) -> Any:
    """Shared salvage logic for a reply that is supposed to be JSON."""
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


def ask_json(prompt: str, *, system: str = SYSTEM_PROMPT) -> Any:
    """One Hermes round-trip that must come back as JSON."""
    try:
        raw = _run(lambda: hermes.complete(prompt, system=system))
    except hermes.HermesError as exc:
        raise LLMUnavailable(str(exc)) from exc
    return _parse_json(raw)


def ask_agent_json(
    prompt: str, *, instructions: str | None = None, timeout: float = 120.0
) -> tuple[Any, list[str]]:
    """One Hermes agent run that must come back as JSON.

    Unlike `ask_json`, this goes through the Runs API rather than a bare
    completion -- the caller needs whatever real tools (search, browsing) the
    gateway has, not the model's unaided guess. 120s, not the client's 900s
    default: this runs synchronously inside a roadmap node, once per
    ungrounded milestone, so a slow run should time out and fall back rather
    than stall the whole approval screen.

    Returns the parsed reply plus one "tool: gist" string per tool the run
    actually completed, so a caller can show what was consulted instead of
    asserting it.
    """
    try:
        result = _run(lambda: hermes.run(prompt, instructions=instructions, timeout=timeout))
    except hermes.HermesError as exc:
        raise LLMUnavailable(str(exc)) from exc

    error = next((e["text"] for e in result.trace if e.get("kind") == "error"), None)
    if error:
        raise LLMUnavailable(f"Hermes run failed: {error}")

    sources = [
        f"{entry['tool']}: {entry['output']}"
        for entry in result.trace
        if entry.get("kind") == "tool" and entry.get("status") == "ok" and entry.get("output")
    ]
    return _parse_json(result.output), sources
