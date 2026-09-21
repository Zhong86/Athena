#!/usr/bin/env python
"""Query the Athena SQLite DB without the sqlite3 CLI.

    python scripts/db.py                      # interactive REPL
    python scripts/db.py "select * from sessions"
    python scripts/db.py .tables
    python scripts/db.py .schema sessions

Honours SQLITE_PATH, so it points at whichever DB the app is using.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

WRITE_PREFIXES = ("insert", "update", "delete", "drop", "alter", "create", "replace")


def render(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0].keys())
    table = [cols] + [
        ["" if r[c] is None else str(r[c]).replace("\n", "\\n") for c in cols]
        for r in rows
    ]
    widths = [min(max(len(r[i]) for r in table), 60) for i in range(len(cols))]
    out = []
    for n, row in enumerate(table):
        out.append("  ".join(c[:w].ljust(w) for c, w in zip(row, widths)))
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out) + f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})"


def run(conn: sqlite3.Connection, sql: str) -> str:
    sql = sql.strip().rstrip(";")
    if not sql:
        return ""

    if sql == ".tables":
        sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    elif sql.startswith(".schema"):
        parts = sql.split()
        target = parts[1] if len(parts) > 1 else None
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            + (" AND tbl_name = ?" if target else ""),
            (target,) if target else (),
        ).fetchall()
        return "\n\n".join(r["sql"] for r in rows) or "(nothing found)"
    elif sql == ".counts":
        names = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return "\n".join(
            f"{n:<20} {conn.execute(f'SELECT COUNT(*) FROM {n}').fetchone()[0]}"
            for n in names
        )

    try:
        cur = conn.execute(sql)
    except sqlite3.Error as exc:
        return f"error: {exc}"

    if sql.lower().startswith(WRITE_PREFIXES):
        conn.commit()
        return f"ok ({cur.rowcount} row{'s' if cur.rowcount != 1 else ''} affected)"

    rows = cur.fetchall()
    # JSON columns are unreadable inline; pretty-print single-cell results.
    if len(rows) == 1 and len(rows[0].keys()) == 1:
        value = rows[0][0]
        if isinstance(value, str) and value.startswith(("{", "[")):
            try:
                return json.dumps(json.loads(value), indent=2)
            except json.JSONDecodeError:
                pass
    return render(rows)


def main() -> None:
    path = get_settings().sqlite_path
    if not path.exists():
        print(f"no database at {path} -- start the backend once to create it")
        raise SystemExit(1)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if len(sys.argv) > 1:
        print(run(conn, " ".join(sys.argv[1:])))
        return

    print(f"{path}\n.tables  .schema [table]  .counts  .quit\n")
    while True:
        try:
            line = input("sqlite> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in (".quit", ".exit"):
            break
        if line.strip():
            print(run(conn, line))


if __name__ == "__main__":
    main()
