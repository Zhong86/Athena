-- The "gather" LangGraph's own run log -- distinct from `connections`'s
-- `last_synced_at`, which is stamped by every ordinary Drive fetch during
-- ingest (see materials/ingest/pipeline.py) and would let an unrelated manual
-- import silently advance the gather cursor if reused here.
--
-- `drive_cursor` is only written on a run that finished a *complete* Drive
-- scan (see materials/gather/scan_drive.py) -- if the scan cap was hit
-- mid-page, the next run re-scans the same window rather than silently
-- skipping files past the cap.

CREATE TABLE materials_gather_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at     TEXT,
    drive_cursor    TEXT,
    candidates_seen INTEGER NOT NULL DEFAULT 0,
    imported_count  INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE INDEX idx_materials_gather_runs_started ON materials_gather_runs (started_at DESC);
