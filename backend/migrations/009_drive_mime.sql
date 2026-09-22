-- Step 10 — the Drive bridge: remember how to re-fetch a Drive file.
--
-- 005 added the pointer (drive_file_id, drive_url, drive_modified_at) but not
-- the file's Drive mime type, and `upload_type` cannot stand in for it: a
-- Google Doc and a .txt are both upload_type='text', yet the first has no bytes
-- of its own and must go through /export?mimeType=..., while the second is
-- fetched with alt=media. Since a Drive row keeps nothing on disk, every ingest
-- and every retry re-fetches -- so the distinction has to survive on the row.
--
-- Additive ADD COLUMN only. Widening the `ingest_status` CHECK to carry a
-- 'fetching' stage (as 005's comment anticipated) would mean rebuilding
-- source_files, and chunks.source_file_id is ON DELETE CASCADE -- dropping the
-- table mid-migration would take every chunk in the database with it, and
-- PRAGMA foreign_keys is a no-op inside the transaction the runner wraps each
-- script in (app/migrations.py). Drive fetching therefore happens inside the
-- existing 'extracting' stage, and the UI labels it from `origin`.

ALTER TABLE source_files ADD COLUMN drive_mime_type TEXT;
