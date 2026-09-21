-- Step 9 — Google Drive as a source origin (schema + read path only).
--
-- Per the Drive rescope in plans/athena-materials-backend-plan.md §1: a
-- Drive-origin file keeps *no bytes on the VPS*. We store a pointer and the
-- derived index; the original is theirs and stays in Drive. `stored_path` is
-- therefore NULL for origin='drive', and the ingest pipeline's retry re-fetches
-- from the Drive API instead of reading disk.
--
-- The OAuth flow, the file picker and the `fetching` pipeline stage are not
-- built yet. This migration is what lets a Drive row exist and be rendered once
-- they are, without another schema change.

ALTER TABLE source_files ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'
    CHECK (origin IN ('local', 'drive'));

-- Drive's own id for the file. The unique index is partial because every
-- local upload leaves it NULL, and SQLite treats NULLs as distinct -- so the
-- constraint binds Drive rows only, where it means "one row per Drive file".
ALTER TABLE source_files ADD COLUMN drive_file_id TEXT;

-- Drive's webViewLink, stored rather than derived: the URL shape differs
-- between a binary file (/file/d/<id>/view) and a native Doc or Sheet
-- (/document/d/<id>/edit), and only the API knows which this is.
ALTER TABLE source_files ADD COLUMN drive_url TEXT;

-- What makes staleness detectable: if Drive later reports a newer mtime than
-- this, the file's chunks are out of date and it can be re-ingested.
ALTER TABLE source_files ADD COLUMN drive_modified_at TEXT;

CREATE UNIQUE INDEX idx_source_files_drive ON source_files (drive_file_id)
    WHERE drive_file_id IS NOT NULL;
