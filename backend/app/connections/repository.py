"""SQL for `connections` and `connection_capabilities`.

The one rule that shapes this file: `secret` never leaves it decrypted except
through `get_credentials`. Every other read goes through `_public`, which drops
the column entirely -- so there is no route, schema or log line that could leak
a refresh token by accident (plans/athena-connections-plan_v.0.2.md §5.2).
"""

import json
import sqlite3
from typing import Any

from app.clock import utc_now_iso
from app.connections import crypto

CAPABILITIES = ("drive.read", "notion.read", "web.read")

GOOGLE_SLUG = "google"


def _public(row: sqlite3.Row) -> dict[str, Any]:
    """A connection row with the credential removed and scopes parsed.

    `secret` is dropped rather than masked: a masked field is still a field,
    and fields get logged.
    """
    data = dict(row)
    data.pop("secret", None)
    try:
        data["scopes"] = json.loads(data.get("scopes") or "[]")
    except json.JSONDecodeError:
        data["scopes"] = []
    return data


def get(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM connections WHERE slug = ?", (slug,)).fetchone()
    return _public(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM connections ORDER BY provider, slug"
    ).fetchall()
    out = []
    for row in rows:
        data = _public(row)
        data["capabilities"] = capabilities_for(conn, row["id"])
        out.append(data)
    return out


def with_capabilities(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    data = get(conn, slug)
    if data is None:
        return None
    data["capabilities"] = capabilities_for(conn, data["id"])
    return data


def has_secret(conn: sqlite3.Connection, slug: str) -> bool:
    row = conn.execute(
        "SELECT secret IS NOT NULL AS present FROM connections WHERE slug = ?", (slug,)
    ).fetchone()
    return bool(row and row["present"])


def get_credentials(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """The only decrypting read. Provider clients call this; routes do not
    pass its result outward."""
    row = conn.execute(
        "SELECT secret FROM connections WHERE slug = ?", (slug,)
    ).fetchone()
    if row is None or row["secret"] is None:
        return None
    return crypto.decrypt(row["secret"])


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def upsert_authorizing(
    conn: sqlite3.Connection,
    *,
    slug: str,
    provider: str,
    display_name: str,
    secret: dict[str, Any],
) -> dict[str, Any]:
    """Start (or restart) a connect flow.

    Re-uploading a client JSON must update the existing row -- the unique index
    on `slug` guarantees one row per provider account, and a second Google row
    would leave `access.require` picking arbitrarily between them. Clears
    `last_error` so a previous failure does not haunt the new attempt.
    """
    conn.execute(
        """
        INSERT INTO connections
            (provider, slug, display_name, auth_type, status, secret, scopes)
        VALUES (?, ?, ?, 'oauth2', 'authorizing', ?, '[]')
        ON CONFLICT (slug) DO UPDATE SET
            display_name = excluded.display_name,
            status       = 'authorizing',
            secret       = excluded.secret,
            last_error   = NULL
        """,
        (provider, slug, display_name, crypto.encrypt(secret)),
    )
    return get(conn, slug)  # type: ignore[return-value]


def mark_connected(
    conn: sqlite3.Connection,
    *,
    slug: str,
    secret: dict[str, Any],
    account_label: str | None,
    scopes: list[str],
    expires_at: str,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE connections
           SET status        = 'connected',
               secret        = ?,
               account_label = ?,
               scopes        = ?,
               expires_at    = ?,
               connected_at  = ?,
               last_error    = NULL
         WHERE slug = ?
        """,
        (
            crypto.encrypt(secret),
            account_label,
            json.dumps(scopes),
            expires_at,
            utc_now_iso(),
            slug,
        ),
    )
    return get(conn, slug)  # type: ignore[return-value]


def update_token(
    conn: sqlite3.Connection,
    *,
    slug: str,
    secret: dict[str, Any],
    expires_at: str,
) -> None:
    """Persist a refreshed access token.

    Deliberately narrower than `mark_connected`: a refresh renews access, it
    does not re-establish the connection, so `connected_at`, `scopes` and
    `account_label` are left alone. Status is forced back to 'connected'
    because a successful refresh disproves a previous 'expired' or 'error'.
    """
    conn.execute(
        """
        UPDATE connections
           SET secret     = ?,
               expires_at = ?,
               status     = 'connected',
               last_error = NULL
         WHERE slug = ?
        """,
        (crypto.encrypt(secret), expires_at, slug),
    )


def mark_synced(conn: sqlite3.Connection, slug: str) -> None:
    """Stamp `last_synced_at` after a successful read, so Settings can show
    when Αθηνα last actually used the connection rather than when it was made."""
    conn.execute(
        "UPDATE connections SET last_synced_at = ? WHERE slug = ?",
        (utc_now_iso(), slug),
    )


def mark_error(conn: sqlite3.Connection, slug: str, message: str) -> None:
    conn.execute(
        "UPDATE connections SET status = 'error', last_error = ? WHERE slug = ?",
        (message[:500], slug),
    )


def clear(conn: sqlite3.Connection, slug: str, *, note: str | None = None) -> None:
    """Disconnect: drop the credential, keep the row and its history.

    Capability rows go too -- leaving `drive.read` enabled on a disconnected
    connection would make the Settings toggle lie about what Αθηνα can do.
    """
    row = conn.execute("SELECT id FROM connections WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return
    conn.execute(
        "DELETE FROM connection_capabilities WHERE connection_id = ?", (row["id"],)
    )
    conn.execute(
        """
        UPDATE connections
           SET status = 'disconnected',
               secret = NULL,
               scopes = '[]',
               expires_at = NULL,
               connected_at = NULL,
               account_label = NULL,
               last_error = ?
         WHERE slug = ?
        """,
        (note, slug),
    )


# --------------------------------------------------------------------------
# capabilities
# --------------------------------------------------------------------------


def capabilities_for(conn: sqlite3.Connection, connection_id: int) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT capability, enabled FROM connection_capabilities WHERE connection_id = ?",
        (connection_id,),
    ).fetchall()
    return {r["capability"]: bool(r["enabled"]) for r in rows}


def grant_capability(
    conn: sqlite3.Connection, connection_id: int, capability: str
) -> None:
    """Create the row if the scope backing it was granted. Enabling is a
    separate act -- see `set_capability`."""
    conn.execute(
        """
        INSERT INTO connection_capabilities (connection_id, capability, enabled)
        VALUES (?, ?, 1)
        ON CONFLICT (connection_id, capability) DO UPDATE SET enabled = 1
        """,
        (connection_id, capability),
    )


def set_capability(
    conn: sqlite3.Connection, connection_id: int, capability: str, enabled: bool
) -> bool:
    """Toggle an existing capability. False means there is no such row, which
    means the scope was never granted -- the caller turns that into a 404
    rather than silently creating permission that Google did not give."""
    cur = conn.execute(
        "UPDATE connection_capabilities SET enabled = ? WHERE connection_id = ? AND capability = ?",
        (1 if enabled else 0, connection_id, capability),
    )
    return cur.rowcount > 0
