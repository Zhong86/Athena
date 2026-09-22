-- External accounts and sites Αθηνα can reach, per plans/athena-connections-plan_v.0.2.md §2.
--
-- One table family rather than one per provider: every consumer asks the same
-- four questions (connected? what may it do? last sync? did it break?), and the
-- credential blob is the only provider-shaped part -- which is opaque anyway.
-- Adding a fourth provider is a CHECK widening plus a client module.
--
-- The plan numbered this 004; 004-007 were taken by quizzes, drive sources,
-- session titles and the calendar drop, so it lands at 008.

CREATE TABLE connections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider      TEXT NOT NULL
        CHECK (provider IN ('google', 'notion', 'web')),
    -- Stable machine name, referenced by code rather than by id: 'google',
    -- 'notion', 'web:portal.university.ac.id'. Unique because reconnecting
    -- must update the existing row, never create a second one.
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,              -- "Google", "Campus portal"
    -- Which account/site this is, for the UI. Never used as a key.
    account_label TEXT,                       -- "you@gmail.com", workspace, hostname
    -- provider='web' only: the origin the agent may fetch under. NULL otherwise.
    base_url      TEXT,
    auth_type     TEXT NOT NULL
        CHECK (auth_type IN ('oauth2', 'token', 'none')),
    -- 'authorizing' is this migration's one deviation from the plan: it is the
    -- state between "client JSON uploaded" and "code exchanged". Without it a
    -- half-finished connect has nowhere to live and the UI cannot offer to
    -- resume it after a reload -- the user would have to re-upload the file.
    status        TEXT NOT NULL DEFAULT 'disconnected'
        CHECK (status IN ('disconnected', 'authorizing', 'connected', 'expired', 'error')),
    -- JSON array of granted OAuth scopes, verbatim from the provider. This is
    -- what was *granted*; connection_capabilities is what the user *allows*.
    scopes        TEXT NOT NULL DEFAULT '[]',
    -- Encrypted JSON credential blob. Shape is provider-private. NEVER leaves
    -- the repository layer -- see the plan's section 5.
    secret        TEXT,
    -- Access-token expiry, so the refresher can find work with one indexed
    -- query instead of decrypting every row.
    expires_at    TEXT,
    last_synced_at TEXT,
    last_error    TEXT,                        -- cleared on the next success
    connected_at  TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_connections_provider ON connections (provider);
CREATE INDEX idx_connections_expiry ON connections (expires_at)
    WHERE status = 'connected';

-- What this connection is ALLOWED to do, one row per capability. This is the
-- table that gates behaviour -- every external call checks it first.
--
-- Not redundant with `scopes`: scopes is what the provider granted, this is
-- what the user has since permitted. Granted is not permitted. A capability row
-- may exist and be disabled; a capability whose scope was never granted must
-- not exist at all.
CREATE TABLE connection_capabilities (
    connection_id INTEGER NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    capability    TEXT NOT NULL CHECK (capability IN (
        'drive.read',
        'notion.read',
        'web.read'
    )),
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    PRIMARY KEY (connection_id, capability)
);

-- Provenance: which connection a Drive-origin file came from.
--
-- SET NULL, not CASCADE, and this is deliberate: disconnecting Google must not
-- delete the user's topic scores. A Drive-sourced source_file whose connection
-- is gone keeps its chunks, its embeddings and its tagging -- it just becomes
-- un-refreshable. CASCADE would silently wipe half the materials index on a
-- disconnect click, and with it every user_understanding score derived from it.
ALTER TABLE source_files ADD COLUMN connection_id INTEGER
    REFERENCES connections (id) ON DELETE SET NULL;

-- Nothing is seeded. The 'google' row is created by the first client-JSON
-- upload, and provider='web' rows are just an allowlist the user edits.
