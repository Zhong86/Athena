-- Chats are renamed, archived and deleted from the sessions list, so the
-- derived title (first user message) needs somewhere to be overridden, and
-- archived rows need a flag that keeps them out of the default listing
-- without destroying the transcript.
ALTER TABLE sessions ADD COLUMN title TEXT;
ALTER TABLE sessions ADD COLUMN archived_at TEXT;

CREATE INDEX idx_sessions_archived_at ON sessions (archived_at);
