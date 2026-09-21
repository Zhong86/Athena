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


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.api_server_key}",
        "X-Hermes-Session-Key": settings.hermes_session_key,
        "Content-Type": "application/json",
    }


async def ping() -> bool:
    """Liveness probe against the Hermes gateway. Unauthenticated."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.hermes_base_url}/health")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def complete(prompt: str, *, system: str | None = None) -> str:
    """Trivial one-shot round-trip via the OpenAI-compatible endpoint."""
    settings = get_settings()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{settings.hermes_base_url}/v1/chat/completions",
            headers=_headers(),
            json={"model": "hermes-agent", "messages": messages, "stream": False},
        )

    if resp.status_code != 200:
        raise HermesError(f"Hermes returned {resp.status_code}: {resp.text[:500]}")

    payload = resp.json()
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise HermesError(f"Unexpected response shape: {payload!r}") from exc
