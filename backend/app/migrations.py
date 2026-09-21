"""Numbered SQL migration runner.

No ORM in this project, so Alembic's autogenerate buys nothing. Migrations are
plain `NNN_name.sql` files applied in filename order, each recorded in
`schema_migrations` so re-running is a no-op.
"""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
)
"""


def _pending(conn: sqlite3.Connection) -> list[Path]:
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.stem not in applied]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every pending migration. Returns the versions applied."""
    conn.execute(_TRACKING_TABLE)

    applied: list[str] = []
    for path in _pending(conn):
        version = path.stem.replace("'", "''")
        # executescript() implicitly commits any pending transaction, so BEGIN
        # and COMMIT have to live inside the script itself. SQLite DDL is
        # transactional, so a failure anywhere rolls the whole file back --
        # including the version row, which is why it is stamped in here too.
        script = (
            "BEGIN;\n"
            f"{path.read_text()}\n"
            f"INSERT INTO schema_migrations (version) VALUES ('{version}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"migration failed: {path.name}: {exc}") from exc
        applied.append(path.stem)

    return applied


def current_version(conn: sqlite3.Connection) -> str | None:
    conn.execute(_TRACKING_TABLE)
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row["version"] if row else None


def pending_count(conn: sqlite3.Connection) -> int:
    conn.execute(_TRACKING_TABLE)
    return len(_pending(conn))
