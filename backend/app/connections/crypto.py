"""Fernet over `connections.secret`.

The invariant from plans/athena-connections-plan_v.0.2.md §5.1: with no key
configured, a connect flow is *refused*. It does not silently fall back to
storing the credential in plaintext -- a missing env var must not quietly
downgrade the security of a stored refresh token.
"""

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretKeyMissing(RuntimeError):
    """No CONNECTIONS_SECRET_KEY. Callers turn this into a readable 409."""


class SecretUnreadable(RuntimeError):
    """The stored blob will not decrypt -- almost always a rotated key."""


@lru_cache
def _fernet(key: str) -> Fernet:
    return Fernet(key.encode())


def _cipher() -> Fernet:
    key = get_settings().connections_secret_key
    if not key:
        raise SecretKeyMissing(
            "CONNECTIONS_SECRET_KEY is not set, so credentials cannot be stored "
            "safely. Generate one with: python -c \"from cryptography.fernet "
            'import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return _fernet(key)
    except (ValueError, TypeError) as exc:
        raise SecretKeyMissing(
            "CONNECTIONS_SECRET_KEY is not a valid Fernet key -- it must be "
            "32 url-safe base64-encoded bytes."
        ) from exc


def available() -> bool:
    """Whether a connect flow can start at all. Lets the UI say so up front
    instead of failing the user after they have picked a file."""
    try:
        _cipher()
    except SecretKeyMissing:
        return False
    return True


def encrypt(payload: dict[str, Any]) -> str:
    return _cipher().encrypt(json.dumps(payload).encode()).decode()


def decrypt(blob: str) -> dict[str, Any]:
    try:
        return json.loads(_cipher().decrypt(blob.encode()))
    except InvalidToken as exc:
        raise SecretUnreadable(
            "the stored credential could not be decrypted -- "
            "CONNECTIONS_SECRET_KEY has changed since it was saved. "
            "Disconnect and reconnect to fix this."
        ) from exc
