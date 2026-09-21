-- Step 6 -- Goal roadmap.
--
-- 001 already created `goals` and `milestones` with the spec's field names. What
-- it lacks: the goal-level fields the pages render, the per-milestone copy the
-- accordion expands to, and an index of in-flight LangGraph runs. All additive.

-- goals: fields goal-list and goal-detail actually render
ALTER TABLE goals ADD COLUMN short_name TEXT;        -- nav label: "Thermo midterm"
ALTER TABLE goals ADD COLUMN course_code TEXT;       -- "CHEM 2010", NULL for career goals
ALTER TABLE goals ADD COLUMN category TEXT NOT NULL DEFAULT 'academic'
    CHECK (category IN ('academic', 'career'));
ALTER TABLE goals ADD COLUMN due_at TEXT;            -- goal deadline, ISO-8601
ALTER TABLE goals ADD COLUMN derivation TEXT;        -- "Derived from your syllabus and..."
ALTER TABLE goals ADD COLUMN order_rationale TEXT;   -- goal-level "why this order" copy
ALTER TABLE goals ADD COLUMN updated_at TEXT;        -- drives "Last updated 2 minutes ago"

-- goals.status stays exactly as 001 declared it: draft|committed|archived.
-- goal-list.html styles a fourth state (Paused) but never renders one, and
-- SQLite cannot widen a CHECK without rebuilding the table -- which means
-- dropping `goals`, and with foreign_keys ON that cascades every milestone row
-- away. Not worth that for an unused pill: `archived` covers "not active". If
-- Paused ever becomes load-bearing it needs a real rebuild migration.

-- milestones: the collapsed row needs `reason`; the expanded accordion needs the rest
ALTER TABLE milestones ADD COLUMN reason_long TEXT;       -- accordion body
ALTER TABLE milestones ADD COLUMN est_effort_min INTEGER; -- "45-60 min" -> 45
ALTER TABLE milestones ADD COLUMN est_effort_max INTEGER; -- "45-60 min" -> 60
-- The accordion names the prerequisite, so it has to resolve to a real
-- milestone rather than being prose inside `reason`. SET NULL means rejecting a
-- milestone clears the pointer instead of orphaning it.
ALTER TABLE milestones ADD COLUMN unlocks_after_id INTEGER
    REFERENCES milestones (id) ON DELETE SET NULL;
ALTER TABLE milestones ADD COLUMN progress_status TEXT NOT NULL DEFAULT 'upcoming'
    CHECK (progress_status IN ('upcoming', 'current', 'done'));
-- Provenance for `N chunks tagged "Topic"` and for the focus milestone's "See
-- related materials". Stored, not re-searched: the vectors can move.
ALTER TABLE milestones ADD COLUMN source_chunk_ids TEXT NOT NULL DEFAULT '[]';
-- The graph reorders and rejects by a stable string id generated at
-- decomposition time, long before any row exists. Kept so a committed row can
-- be traced back to the draft the user approved.
ALTER TABLE milestones ADD COLUMN state_id TEXT;

-- One row per in-flight creation run. The graph's own state lives in the
-- checkpointer; this is only the index the HTTP layer looks runs up by.
CREATE TABLE roadmap_runs (
    thread_id  TEXT PRIMARY KEY,
    goal_id    INTEGER REFERENCES goals (id) ON DELETE CASCADE,  -- NULL until commit
    status     TEXT NOT NULL CHECK (status IN
                   ('clarifying', 'decomposing', 'awaiting_approval',
                    'committed', 'abandoned')),
    raw_goal_input TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_roadmap_runs_status ON roadmap_runs (status, updated_at);
CREATE INDEX idx_milestones_state_id ON milestones (goal_id, state_id);
