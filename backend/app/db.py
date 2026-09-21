import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import get_settings


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.sqlite_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Single-user system — one connection per request is plenty."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Touch the database file so /health can report on it.

    Schema itself lands in Step 2; this only guarantees the file and its
    parent directory exist.
    """
    with connection():
        pass
