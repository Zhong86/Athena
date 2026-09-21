-- Step 4 -- Quizzes.
--
-- 001 already created `quiz_attempts`: one row per answered question, with a
-- boolean `correct`. That shape holds for multiple choice and nothing else. An
-- open-ended answer is partially right, is graded by Hermes rather than by a
-- key, and has to keep the reasoning alongside the number -- otherwise the
-- score is a bare assertion the user cannot argue with. So 001's table stays as
-- the *answer* row and gains the grading columns, while the quiz itself and its
-- questions get tables of their own.
--
-- Generation is deliberately out of scope: a quiz arrives fully formed. What
-- matters at this layer is that whatever produced it also hands over the
-- resources it used, because grading an open-ended answer means putting those
-- same resources back in front of Hermes. A grade produced from material the
-- quiz was not written from is not reviewable.

CREATE TABLE quizzes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Every quiz is also a row in the Sessions log, so a quiz appears next to
    -- chats and cron runs without the log needing to know what a quiz is. 001
    -- already reserved 'quiz' in sessions.type for exactly this.
    session_id INTEGER NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    -- NOT NULL: a quiz exists to measure one topic's understanding, and
    -- quiz_attempts.topic_id is NOT NULL, so there has to be a fallback for a
    -- question that does not name its own. Per-question overrides live on
    -- quiz_questions.topic_id.
    topic_id   INTEGER NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'in_progress', 'grading', 'graded')),
    -- JSON array of resource objects: {"kind":"chunk","chunk_id":N} for our own
    -- material, {"kind":"external","url":...,"title":...,"text":...} for
    -- anything the generator pulled in from outside. Chunk entries are just
    -- pointers (the text is rehydrated from `chunks` at grading time, so an
    -- edited chunk grades against its current text); external entries carry
    -- their own excerpt because the backend has no outbound web access and
    -- cannot re-fetch a page that may have changed or vanished.
    resources  TEXT NOT NULL DEFAULT '[]',
    -- 0..100. NULL until graded. NULL is also where a quiz stays if grading
    -- finished degraded -- see grading_error.
    score      INTEGER CHECK (score BETWEEN 0 AND 100),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    submitted_at TEXT,
    graded_at    TEXT,
    -- Set when the open-ended pass could not complete (gateway down, unusable
    -- reply). The quiz still shows the answers it managed to grade, and the
    -- topic's understanding is left untouched rather than moved on half the
    -- evidence.
    grading_error TEXT
);

CREATE INDEX idx_quizzes_topic ON quizzes (topic_id, created_at DESC);
CREATE INDEX idx_quizzes_status ON quizzes (status, created_at DESC);
CREATE UNIQUE INDEX idx_quizzes_session ON quizzes (session_id);

CREATE TABLE quiz_questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id     INTEGER NOT NULL REFERENCES quizzes (id) ON DELETE CASCADE,
    -- Same naming as milestones.order_index, and for the same reason: "order"
    -- is a SQL keyword that would need quoting at every call site.
    order_index INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('multiple_choice', 'open_ended')),
    prompt      TEXT NOT NULL,
    -- JSON array of option strings. Empty for open_ended.
    options     TEXT NOT NULL DEFAULT '[]',
    -- Index into `options`. NULL for open_ended, where there is no key.
    correct_option INTEGER,
    -- What a full-credit answer has to cover. Open-ended only; this is what
    -- Hermes grades against, together with the resources.
    rubric      TEXT,
    -- Shown after grading, for both kinds. Written at generation time so a
    -- multiple-choice quiz can explain itself without another model call.
    explanation TEXT,
    -- Narrows the quiz-level resources for this one question. Empty means
    -- "grade against the whole quiz's resources".
    resources   TEXT NOT NULL DEFAULT '[]',
    -- Overrides quizzes.topic_id for a mixed-topic quiz. NULL is the common case.
    topic_id    INTEGER REFERENCES topics (id) ON DELETE SET NULL,
    UNIQUE (quiz_id, order_index)
);

CREATE INDEX idx_quiz_questions_quiz ON quiz_questions (quiz_id, order_index);

-- quiz_attempts: 001's answer row, extended for the two grading paths.
-- All additive. `correct` and `question` keep their 001 meaning: `question` is
-- the prompt text snapshotted at answer time, so the Sessions log still reads
-- correctly after a quiz is deleted.
ALTER TABLE quiz_attempts ADD COLUMN quiz_id INTEGER
    REFERENCES quizzes (id) ON DELETE CASCADE;
ALTER TABLE quiz_attempts ADD COLUMN question_id INTEGER
    REFERENCES quiz_questions (id) ON DELETE CASCADE;
-- Plain TEXT, no CHECK: SQLite cannot widen a CHECK without rebuilding the
-- table, and a third question kind is far likelier here than a fourth goal
-- status. quiz_questions.kind is the constrained copy.
ALTER TABLE quiz_attempts ADD COLUMN kind TEXT;
-- Multiple choice only: index into quiz_questions.options.
ALTER TABLE quiz_attempts ADD COLUMN selected_option INTEGER;
-- 0..100 for both kinds. A multiple-choice answer scores 100 or 0, which is
-- what lets one average produce the quiz score without special-casing.
ALTER TABLE quiz_attempts ADD COLUMN score INTEGER;
ALTER TABLE quiz_attempts ADD COLUMN feedback TEXT;
-- 'key' (compared against correct_option) or 'hermes' (graded by the model).
-- NULL means this answer was never graded.
ALTER TABLE quiz_attempts ADD COLUMN graded_by TEXT;
-- The resources Hermes actually cited for this grade -- the "why this score"
-- the Materials page promises. Empty for key-graded answers.
ALTER TABLE quiz_attempts ADD COLUMN grading_resources TEXT NOT NULL DEFAULT '[]';
ALTER TABLE quiz_attempts ADD COLUMN graded_at TEXT;

-- One answer per question per quiz. Partial so 001-era rows, which have no
-- quiz_id, do not all collide on (NULL, NULL).
CREATE UNIQUE INDEX idx_quiz_attempts_question
    ON quiz_attempts (quiz_id, question_id) WHERE quiz_id IS NOT NULL;

-- Every change to topics.user_understanding, with the evidence that caused it.
-- The implementation plan's done-condition for this step is that a quiz
-- round-trip moves a topic's score *and the cause is queryable*, so the write
-- and the reason are recorded in the same transaction. Without this table the
-- number on the Materials page is decorative.
CREATE TABLE understanding_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id   INTEGER NOT NULL REFERENCES topics (id) ON DELETE CASCADE,
    source     TEXT NOT NULL CHECK (source IN ('quiz', 'session', 'manual')),
    -- SET NULL, not CASCADE: deleting a quiz must not erase the history of the
    -- score it produced. `reason` is prose and survives on its own.
    quiz_id    INTEGER REFERENCES quizzes (id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES sessions (id) ON DELETE SET NULL,
    -- -1 when this was the topic's first signal, matching topics.user_understanding.
    previous_understanding INTEGER NOT NULL,
    understanding INTEGER NOT NULL CHECK (understanding BETWEEN 0 AND 100),
    reason     TEXT NOT NULL,
    -- JSON: the numbers behind `reason` (raw quiz score, per-question results,
    -- blend weight) so the sentence can be checked rather than trusted.
    evidence   TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_understanding_events_topic
    ON understanding_events (topic_id, created_at DESC);
