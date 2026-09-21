"""One timestamp format, everywhere.

Matches the `strftime('%Y-%m-%dT%H:%M:%SZ','now')` column defaults in the
migrations, so rows written by Python and rows written by SQLite sort and render
identically. Columns added via ALTER TABLE cannot carry that default (SQLite
only allows constant defaults there), which is exactly why callers need this.
"""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
