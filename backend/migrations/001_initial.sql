-- Step 2 — initial relational schema.
-- Everything relational lives here; LanceDB holds only chunk embedding vectors.
-- Single-user system: no owner/tenant columns anywhere.

CREATE TABLE source_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    upload_type TEXT NOT NULL CHECK (upload_type IN ('text', 'pdf', 'image')),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE topics (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL UNIQUE,
    description        TEXT,
    -- -1 = unsure, no signal yet. 0..100 once a quiz or session scores it.
    user_understanding INTEGER NOT NULL DEFAULT -1
        CHECK (user_understanding = -1 OR user_understanding BETWEEN 0 AND 100)
);

CREATE TABLE chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_id INTEGER NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
    -- MVP: one chunk = exactly one topic. NULL only while tagging is pending.
    topic_id       INTEGER REFERENCES topics (id) ON DELETE SET NULL,
    text           TEXT NOT NULL,
    -- LanceDB row id. NULL until the embedding pass has run.
    embedding_ref  TEXT UNIQUE
);

CREATE INDEX idx_chunks_topic ON chunks (topic_id);
CREATE INDEX idx_chunks_source_file ON chunks (source_file_id);

CREATE TABLE sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL CHECK (type IN ('chat', 'quiz', 'cron', 'agent_action')),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload    TEXT,  -- JSON blob, shape varies by type
    summary    TEXT
);

CREATE INDEX idx_sessions_started_at ON sessions (started_at DESC);
CREATE INDEX idx_sessions_type ON sessions (type);

CREATE TABLE quiz_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    topic_id   INTEGER NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
    question   TEXT NOT NULL,
    answer     TEXT,
    correct    INTEGER CHECK (correct IN (0, 1)),  -- NULL until graded
    timestamp  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_quiz_attempts_topic ON quiz_attempts (topic_id);
CREATE INDEX idx_quiz_attempts_session ON quiz_attempts (session_id);

CREATE TABLE goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    -- mirrors RoadmapState.status; 'committed' is what commit_roadmap writes
    status      TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'committed', 'archived')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE milestones (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id           INTEGER NOT NULL REFERENCES goals (id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    description       TEXT,
    -- Named order_index rather than "order" (a SQL keyword needing quotes at
    -- every call site). The Milestone TypedDict field is `order`; map between
    -- the two in the row<->state helpers, not in raw SQL.
    order_index       INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'edited', 'rejected')),
    reason            TEXT,
    source            TEXT CHECK (source IN ('materials', 'research')),
    related_topic_ids TEXT NOT NULL DEFAULT '[]',  -- JSON array of topic ids
    est_effort        TEXT
);

CREATE INDEX idx_milestones_goal ON milestones (goal_id, order_index);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,  -- page-scoped: materials.* | goal.* | general.*
    value TEXT NOT NULL      -- JSON-encoded so booleans survive round-trips
);

CREATE TABLE calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL CHECK (source IN ('google', 'portal')),
    title       TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    raw_payload TEXT
);

CREATE INDEX idx_calendar_events_due_at ON calendar_events (due_at);
