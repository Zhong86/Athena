"""Thin client over the Hermes Agent `api_server` adapter.

Shapes follow https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- auth: `Authorization: Bearer <API_SERVER_KEY>`
- long-term memory scope: `X-Hermes-Session-Key` (single fixed key, single-user system)
- default listen port: 8642
"""

import httpx

from app.config import get_settings


class HermesError(RuntimeError):
    pass


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
