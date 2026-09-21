"""Thin client over the Hermes Agent `api_server` adapter.

Shapes follow https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- auth: `Authorization: Bearer <API_SERVER_KEY>`
- long-term memory scope: `X-Hermes-Session-Key` (single fixed key, single-user system)
- default listen port: 8642
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.clock import utc_now_iso
from app.config import get_settings

# Events that end a run's event stream. Anything else keeps us listening.
TERMINAL_EVENTS = ("run.completed", "run.failed", "run.cancelled", "run.interrupted")


class HermesError(RuntimeError):
    pass


@dataclass
class RunResult:
    """A finished agent run plus the trace we show on Knowledge-Sync."""

    run_id: str
    status: str
    output: str = ""
    trace: list[dict[str, Any]] = field(default_factory=list)


def _headers(session_id: str | None = None) -> dict[str, str]:
    """Session-Key scopes long-term memory and is fixed (single-user system).
    Session-Id scopes the transcript and carries our own session row id, so
    Hermes run status can be correlated back to a row in `sessions`.
    """
    settings = get_settings()
    headers = {
        "X-Hermes-Session-Key": settings.hermes_session_key,
        "Content-Type": "application/json",
    }
    # An empty key would send a bare "Bearer ", which httpx rejects outright as
    # an illegal header value -- surfacing as a confusing transport error
    # rather than the 401 the gateway would actually return.
    if settings.api_server_key:
        headers["Authorization"] = f"Bearer {settings.api_server_key}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers


async def ping() -> bool:
    """Liveness probe against the Hermes gateway. Unauthenticated."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.hermes_base_url}/health")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def chat(
    messages: list[dict[str, str]],
    *,
    session_id: str | None = None,
    system: str | None = None,
) -> str:
    """Run a turn through the OpenAI-compatible endpoint.

    The full transcript is sent every call: the docs describe
    X-Hermes-Session-Id as a correlation handle for external UIs, not a
    promise that the gateway replays history for us. Athena's `sessions`
    row stays the source of truth either way.
    """
    settings = get_settings()
    body: list[dict[str, str]] = []
    if system:
        body.append({"role": "system", "content": system})
    body.extend(messages)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.hermes_base_url}/v1/chat/completions",
                headers=_headers(session_id),
                json={"model": "hermes-agent", "messages": body, "stream": False},
            )
    except httpx.HTTPError as exc:
        raise HermesError(f"Could not reach Hermes at {settings.hermes_base_url}: {exc}") from exc

    if resp.status_code != 200:
        raise HermesError(f"Hermes returned {resp.status_code}: {resp.text[:500]}")

    payload = resp.json()
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise HermesError(f"Unexpected response shape: {payload!r}") from exc


async def complete(prompt: str, *, system: str | None = None) -> str:
    """One-shot round-trip. Thin wrapper over chat() for smoke tests."""
    return await chat([{"role": "user", "content": prompt}], system=system)


# ---------- Runs API: background agent work with a visible trace ----------


def parse_sse(lines: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    """Fold raw SSE lines into (event name, data) pairs.

    Two details from the docs drive this: every stream emits a bare `:
    keepalive` comment whenever 10s pass without an event, and `data:` may be
    split across several lines that concatenate with newlines. Frames with
    unparseable JSON are dropped rather than raising -- one malformed frame
    should not cost us the whole trace of a run that otherwise succeeded.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    data: list[str] = []

    def flush() -> None:
        nonlocal name, data
        if name and data:
            try:
                payload = json.loads("\n".join(data))
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                events.append((name, payload))
        name, data = None, []

    for raw in lines:
        line = raw.rstrip("\r")
        if line.startswith(":"):  # keepalive comment
            continue
        if not line:  # blank line terminates a frame
            flush()
        elif line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].lstrip())

    flush()  # a stream cut short still yields its last complete frame
    return events


def _fold_event(
    trace: list[dict[str, Any]],
    pending: dict[str, dict[str, Any]],
    name: str,
    data: dict[str, Any],
) -> None:
    """Fold one SSE event into the running trace, in place.

    `message.delta` is deliberately ignored: the deltas are the same prose the
    run returns as `output`, and replaying them here would double every
    finding. What we keep is the commentary (`message.interim`) and the shape
    of the work -- which tools ran, how long they took, what broke.
    """
    if name == "message.interim":
        text = (data.get("text") or "").strip()
        if text:
            trace.append({"kind": "note", "at": utc_now_iso(), "text": text})

    elif name == "tool.started":
        tool = data.get("tool") or "tool"
        entry = {
            "kind": "tool",
            "at": utc_now_iso(),
            "tool": tool,
            "input": data.get("preview"),
            "status": "running",
        }
        trace.append(entry)
        # Keyed by tool name so tool.completed can fill in the same entry.
        # Appended at *start* time, which is what keeps the trace ordered.
        pending[tool] = entry

    elif name == "tool.completed":
        tool = data.get("tool") or "tool"
        # A completion with no matching start still deserves a row -- we may
        # have subscribed to the stream after the tool had already begun.
        entry = pending.pop(tool, None)
        if entry is None:
            entry = {"kind": "tool", "at": utc_now_iso(), "tool": tool, "input": None}
            trace.append(entry)
        entry["status"] = "error" if data.get("error") else "ok"
        entry["duration"] = data.get("duration")
        # Gateway caps this preview at 500 chars; it is a gist, not the result.
        entry["output"] = data.get("preview")

    elif name == "subagent.complete":
        trace.append({
            "kind": "subagent",
            "at": utc_now_iso(),
            "status": data.get("status"),
            "summary": data.get("summary"),
            "duration": data.get("duration"),
        })

    elif name == "run.failed":
        trace.append({
            "kind": "error",
            "at": utc_now_iso(),
            "text": data.get("error") or data.get("message") or "Run failed.",
        })


async def _drain_events(client: httpx.AsyncClient, url: str, headers: dict[str, str]):
    """Stream a run's events until a terminal one arrives. Returns the trace."""
    trace: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}

    async with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code != 200:
            await resp.aread()
            raise HermesError(f"Hermes run events returned {resp.status_code}")
        buffer: list[str] = []
        async for line in resp.aiter_lines():
            buffer.append(line)
            if line.rstrip("\r"):  # frame not finished yet
                continue
            for name, data in parse_sse(buffer):
                _fold_event(trace, pending, name, data)
                if name in TERMINAL_EVENTS:
                    return trace
            buffer = []

    for name, data in parse_sse(buffer):  # trailing frame, no blank line
        _fold_event(trace, pending, name, data)
    return trace


async def run(
    prompt: str,
    *,
    session_id: str | None = None,
    instructions: str | None = None,
    timeout: float = 900.0,
) -> RunResult:
    """Start a long-form agent run and collect its trace once it finishes.

    Persist-then-render: we subscribe to the SSE event stream, fold it into a
    trace, and hand the caller a finished object to store. Nothing here is
    live -- Knowledge-Sync reads the stored trace back out of `sessions`.

    Caveat worth knowing: the event subscription opens *after* POST /v1/runs
    returns, and the docs do not say whether the stream replays events that
    fired in between. `tool.completed` without a matching `tool.started` is
    handled for exactly that reason.
    """
    settings = get_settings()
    body: dict[str, Any] = {"input": prompt}
    if session_id:
        body["session_id"] = session_id
    if instructions:
        body["instructions"] = instructions

    headers = _headers(session_id)
    base = settings.hermes_base_url

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            started = await client.post(f"{base}/v1/runs", headers=headers, json=body)
            if started.status_code not in (200, 201, 202):
                raise HermesError(
                    f"Hermes returned {started.status_code}: {started.text[:500]}"
                )
            run_id = started.json().get("run_id")
            if not run_id:
                raise HermesError(f"Run response carried no run_id: {started.text[:500]}")

            trace = await _drain_events(client, f"{base}/v1/runs/{run_id}/events", headers)

            # The event stream carries no final answer, only progress; the
            # run record is where `output` and the settled status live.
            final = await client.get(f"{base}/v1/runs/{run_id}", headers=headers)
    except httpx.HTTPError as exc:
        raise HermesError(f"Could not reach Hermes at {base}: {exc}") from exc

    if final.status_code != 200:
        raise HermesError(f"Hermes returned {final.status_code}: {final.text[:500]}")

    record = final.json()
    return RunResult(
        run_id=run_id,
        status=record.get("status", "completed"),
        output=record.get("output") or "",
        trace=trace,
    )
