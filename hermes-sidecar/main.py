"""Credential drop-box for a Hermes host on a different machine.

Athena's backend needs to put two files into Hermes' config directory:
`google_credentials.json` and `google_token.json`. In production Hermes runs on
the same VPS, so the backend bind-mounts that directory and writes it directly
-- this service is not involved and should not be running.

It exists for the case where Hermes is on another box (debugging): there is no
shared filesystem, and Hermes' own api_server has no endpoint that writes files.

This handles credentials, so it is deliberately small and boring:
  - one bearer token, compared in constant time
  - filenames come from a two-item allowlist, never from the request path
  - atomic 0600 writes into one fixed directory

Run it on loopback and reach it through an SSH tunnel, or put TLS in front. Do
not expose it to the internet: the token is the only thing between a caller and
Hermes' credential store.
"""

import hmac
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response

CONFIG_PATH = Path(os.environ.get("HERMES_CONFIG_PATH", str(Path.home() / ".hermes")))
AUTH_TOKEN = os.environ.get("HERMES_FILES_TOKEN", "")

ALLOWED_FILES = ("google_credentials.json", "google_token.json")

app = FastAPI(title="Hermes credential sidecar", version="0.1.0")


def require_token(authorization: str = Header("")) -> None:
    if not AUTH_TOKEN:
        # Refuse rather than run open: an unauthenticated write endpoint over a
        # credential directory is worse than a broken one.
        raise HTTPException(503, "HERMES_FILES_TOKEN is not set, so writes are disabled")
    expected = f"Bearer {AUTH_TOKEN}"
    # compare_digest, not ==: a short-circuiting comparison leaks the token
    # prefix through response timing.
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "bad or missing bearer token")


def resolve(name: str) -> Path:
    """Allowlist, not sanitisation. `name` arrives from a URL path, and the set
    of legitimate values is two strings -- so traversal is unrepresentable
    rather than filtered out."""
    if name not in ALLOWED_FILES:
        raise HTTPException(404, f"not a credential file this service writes: {name!r}")
    return CONFIG_PATH / name


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "config_path": str(CONFIG_PATH),
        "writable": os.access(CONFIG_PATH, os.W_OK) if CONFIG_PATH.is_dir() else False,
        "auth_configured": bool(AUTH_TOKEN),
        "present": [n for n in ALLOWED_FILES if (CONFIG_PATH / n).exists()],
    }


@app.put("/credentials/{name}", status_code=204, dependencies=[Depends(require_token)])
def put_credential(name: str, payload: dict[str, Any]) -> Response:
    path = resolve(name)
    try:
        CONFIG_PATH.mkdir(parents=True, exist_ok=True)
        os.chmod(CONFIG_PATH, 0o700)
        # Same directory, so os.replace is an atomic rename: Hermes may read
        # these at any moment and must never see a partial file.
        fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise HTTPException(500, f"could not write {name}: {exc}") from exc
    return Response(status_code=204)


@app.delete("/credentials/{name}", status_code=204, dependencies=[Depends(require_token)])
def delete_credential(name: str) -> Response:
    path = resolve(name)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"could not remove {name}: {exc}") from exc
    return Response(status_code=204)
