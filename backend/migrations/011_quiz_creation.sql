-- Quiz creation runs.
--
-- Same relationship to the new quiz-creation graph (app/quizzes/graph.py) that
-- 003's roadmap_runs has to the roadmap graph: the graph's own state lives in
-- its own SqliteSaver file (quiz_creation_checkpoints.db), not here. This is
-- only the index the HTTP layer looks in-flight thread_ids up by, so a parked
-- interrupt survives a closed tab the same way an unfinished roadmap does.

CREATE TABLE quiz_creation_runs (
    thread_id  TEXT PRIMARY KEY,
    -- NULL until commit_quiz runs. SET NULL rather than CASCADE would also be
    -- defensible, but a finished run with its quiz deleted is no longer
    -- "in flight" for anything -- roadmap_runs makes the same CASCADE call.
    quiz_id    INTEGER REFERENCES quizzes (id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN
                   ('choosing_topic', 'choosing_format', 'generating', 'reviewing',
                    'committed', 'abandoned')),
    -- Optional starting hint ("quiz me on entropy") the student typed before
    -- the graph even asked -- echoed back so a reopened run can redraw it.
    topic_hint TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_quiz_creation_runs_status ON quiz_creation_runs (status, updated_at);
