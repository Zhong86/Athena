-- Step 3 — materials ingestion pipeline state.
-- All additive: 001 has the right shape, it just has nowhere to record how far
-- an upload has got through extract -> chunk -> tag -> embed.

-- The ingest state machine lives on source_files, not chunks: the unit the user
-- uploaded and the unit the UI polls are both the file.
ALTER TABLE source_files ADD COLUMN ingest_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (ingest_status IN ('pending','extracting','tagging','embedding','ready','failed'));
ALTER TABLE source_files ADD COLUMN ingest_error TEXT;
ALTER TABLE source_files ADD COLUMN byte_size INTEGER;
-- Where the original bytes were kept, so a failed extract can be retried
-- without a re-upload. Pasted text is written to disk too, so that retry path
-- is identical for every upload type rather than special-casing one of them.
ALTER TABLE source_files ADD COLUMN stored_path TEXT;
ALTER TABLE source_files ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 0;

-- Position of the chunk within its file, so a topic page can show chunks in
-- reading order rather than insertion order. char_start/char_end locate the
-- chunk in the extracted text for future highlight-in-source.
ALTER TABLE chunks ADD COLUMN order_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks ADD COLUMN char_start INTEGER;
ALTER TABLE chunks ADD COLUMN char_end INTEGER;

-- SQLite only allows constant defaults in ALTER TABLE ADD COLUMN, so created_at
-- cannot carry the strftime() default the other tables use. It is added nullable
-- and backfilled here; inserts supply the timestamp explicitly (see
-- materials/repository.py) rather than relying on a column default.
ALTER TABLE topics ADD COLUMN created_at TEXT;
UPDATE topics SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
    WHERE created_at IS NULL;

-- auto_created distinguishes a topic Hermes invented during tagging from one the
-- user named themselves -- the former are the ones worth offering to merge later.
ALTER TABLE topics ADD COLUMN auto_created INTEGER NOT NULL DEFAULT 1
    CHECK (auto_created IN (0, 1));

CREATE INDEX idx_chunks_file_order ON chunks (source_file_id, order_index);
CREATE INDEX idx_source_files_status ON source_files (ingest_status);
