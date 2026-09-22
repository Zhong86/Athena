"""Getting credential files onto the Hermes host.

Hermes reads `google_credentials.json` and `google_token.json` from its config
directory. Its api_server exposes no way to write them -- it speaks only
/health, /v1/chat/completions and /v1/runs* -- so this is a separate channel,
deliberately *not* a Hermes prompt: anything sent as a chat turn lands in
`sessions.payload` as plaintext and is replayed into model context every later
turn (plans/athena-connections-plan_v.0.2.md §4b).

Two modes, because the deployments differ:

- local   -- Hermes on the same VPS. The host's config directory is bind-mounted
             into this container and we write to it. Nothing crosses a network.
- sidecar -- Hermes on another machine (debugging). hermes-sidecar/ runs there
             and accepts authenticated writes.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

# Hardcoded, because the filename reaches here from a route. Deriving a path
# from anything user-influenced is how "../../root/.ssh/authorized_keys"
# happens; an allowlist makes traversal unrepresentable rather than filtered.
GOOGLE_CLIENT_FILE = "google_credentials.json"
GOOGLE_TOKEN_FILE = "google_token.json"
ALLOWED_FILES = (GOOGLE_CLIENT_FILE, GOOGLE_TOKEN_FILE)


class HermesFileError(RuntimeError):
    """Mirrors agent.hermes.HermesError: one error type, mapped to 502."""


def _check_name(name: str) -> None:
    if name not in ALLOWED_FILES:
        # An assertion about our own code, not user input -- the routes only
        # ever pass the two constants above.
        raise HermesFileError(f"refusing to write unexpected file {name!r}")


def describe() -> str:
    """Which mode is active, for error messages and the health endpoint."""
    settings = get_settings()
    if settings.hermes_config_path:
        return f"local ({settings.hermes_config_path})"
    if settings.hermes_files_url:
        return f"sidecar ({settings.hermes_files_url})"
    return "unconfigured"


def configured() -> bool:
    settings = get_settings()
    return bool(settings.hermes_config_path or settings.hermes_files_url)


def _require_configured() -> None:
    if not configured():
        raise HermesFileError(
            "no Hermes credential destination is configured. Set "
            "HERMES_CONFIG_PATH if Hermes runs on this machine, or "
            "HERMES_FILES_URL and HERMES_FILES_TOKEN if it runs elsewhere."
        )


# ---------- local: write the bind-mounted config directory ----------


def _write_local(directory: Path, name: str, payload: dict[str, Any]) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        # Temp file in the *same* directory so os.replace is a rename rather
        # than a cross-device copy, which would not be atomic. Hermes may read
        # these at any moment and must never see a half-written file.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, directory / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise HermesFileError(
            f"could not write {name} to {directory}: {exc}. Check the "
            "directory is mounted into this container and writable."
        ) from exc


def _delete_local(directory: Path, name: str) -> None:
    try:
        (directory / name).unlink(missing_ok=True)
    except OSError as exc:
        raise HermesFileError(f"could not remove {name} from {directory}: {exc}") from exc


# ---------- sidecar: authenticated HTTP to the Hermes box ----------


def _sidecar_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    # Same reasoning as agent.hermes._headers: a bare "Bearer " is an illegal
    # header value and httpx raises a transport error instead of letting the
    # sidecar return its own 401.
    if settings.hermes_files_token:
        headers["Authorization"] = f"Bearer {settings.hermes_files_token}"
    return headers


async def _sidecar_request(method: str, name: str, payload: dict[str, Any] | None) -> None:
    base = get_settings().hermes_files_url.rstrip("/")
    url = f"{base}/credentials/{name}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method, url, headers=_sidecar_headers(), json=payload
            )
    except httpx.HTTPError as exc:
        raise HermesFileError(
            f"could not reach the Hermes sidecar at {base}: {exc}"
        ) from exc

    if resp.status_code not in (200, 204):
        raise HermesFileError(
            f"the Hermes sidecar returned {resp.status_code}: {resp.text[:500]}"
        )


# ---------- the surface the routes use ----------


async def put_file(name: str, payload: dict[str, Any]) -> None:
    _check_name(name)
    _require_configured()
    settings = get_settings()
    if settings.hermes_config_path:
        _write_local(Path(settings.hermes_config_path), name, payload)
    else:
        await _sidecar_request("PUT", name, payload)


async def delete_file(name: str) -> None:
    """Best-effort: a disconnect must finish locally even if the Hermes host is
    unreachable, so callers are expected to catch and report rather than abort."""
    _check_name(name)
    _require_configured()
    settings = get_settings()
    if settings.hermes_config_path:
        _delete_local(Path(settings.hermes_config_path), name)
    else:
        await _sidecar_request("DELETE", name, None)
