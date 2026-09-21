# Connections — schema plan

Scope: one table family that owns **every external account or site Αθηνα can reach** —
Google (Calendar + Drive), Notion, the school website, and any allowlisted research
domain. Replaces the "where does the Drive refresh token live" open item in the
materials plan, and the ad-hoc `general.google_calendar_*` toggles sketched in Step 9.

Locked: `settings` stays flat key/value for *preferences*. Anything with a credential,
an expiry, a sync clock, or a connect/disconnect lifecycle is a connection, not a
setting. That line is the whole reason this table exists — a refresh token must not be
one `GET /settings` away from the frontend.

---

## 1. Why one table and not three

Google, Notion and a school portal have genuinely different auth shapes, which argues
for per-provider tables. Rejected, for three reasons:

- Every consumer wants the same four questions answered — *is it connected, what may it
  do, when did it last sync, did it break?* Those are provider-independent, and the
  Knowledge-Sync page and Settings page both render exactly that list.
- The credential blob is the only genuinely provider-shaped part, and it is opaque to
  everything except the one client module that mints it. Opaque things belong in a
  blob, not in columns.
- Adding provider #4 must be a `CHECK` constraint widening and a new client module, not
  a migration plus a new repository plus a new settings section.

So: one row per connected thing, discriminated by `provider`, with typed columns for
everything queried on and a JSON blob for the rest.

---

## 2. Migration `004_connections.sql`

`003` is taken by the goals roadmap. Note this supersedes the `003_materials_drive.sql`
name used in the materials plan — Drive's `source_files` columns land in `005`, after
this.

```sql
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
    status        TEXT NOT NULL DEFAULT 'disconnected'
        CHECK (status IN ('disconnected', 'connected', 'expired', 'error')),
    -- JSON array of granted OAuth scopes, verbatim from the provider. This is
    -- what was *granted*; connection_capabilities is what the user *allows*.
    scopes        TEXT NOT NULL DEFAULT '[]',
    -- Encrypted JSON credential blob. Shape is provider-private. NEVER leaves
    -- the repository layer -- see section 5.
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
-- table that gates behavior -- every external call checks it first.
CREATE TABLE connection_capabilities (
    connection_id INTEGER NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    capability    TEXT NOT NULL CHECK (capability IN (
        'calendar.read', 'calendar.write',
        'drive.read',
        'notion.read',
        'web.read'
    )),
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    PRIMARY KEY (connection_id, capability)
);
```

`scopes` and `connection_capabilities` look redundant and are not. Google grants Drive
and Calendar in **one consent screen**, so `scopes` will list both the moment the user
connects. `connection_capabilities` is where the user then says "yes to Calendar, not
yet to Drive" — and where the nested Calendar-write sub-toggle from `settings.html`
actually lives. Granted ≠ permitted. A capability row may exist and be disabled; a
capability whose scope was never granted must not exist at all.

### Provenance columns on existing tables

```sql
ALTER TABLE calendar_events ADD COLUMN connection_id INTEGER
    REFERENCES connections (id) ON DELETE SET NULL;
ALTER TABLE source_files ADD COLUMN connection_id INTEGER
    REFERENCES connections (id) ON DELETE SET NULL;
```

`SET NULL`, not `CASCADE`, and this is deliberate: **disconnecting Google must not
delete the user's topic scores.** A Drive-sourced `source_file` whose connection is gone
keeps its chunks, its embeddings and its topic tagging — it just becomes un-refreshable
and un-re-fetchable, which the UI can show as "source disconnected". Cascading here
would silently wipe half the materials index on a disconnect click, and with it every
`user_understanding` score derived from it. Delete-on-disconnect, if ever wanted, is an
explicit second action with its own confirmation.

`calendar_events.source` (`'google'|'portal'`) from 001 stays as the cheap discriminator;
`connection_id` is the precise pointer. Redundant but harmless, and rebuilding the CHECK
on that table is not worth it.

---

## 3. The seeded rows

Migrations insert nothing. Connections are created by the connect flow, with one
exception: `provider='web'` rows with `auth_type='none'` are *just an allowlist*, and
the Settings "websites to access" field is a CRUD view over them. Adding a domain there
writes a `connected` row immediately — there is nothing to authenticate.

| slug | provider | auth_type | capabilities |
|---|---|---|---|
| `google` | google | oauth2 | `calendar.read`, `calendar.write`, `drive.read` |
| `notion` | notion | token | `notion.read` |
| `web:<host>` | web | none | `web.read` |

One `google` row, not one per scope. Re-consenting to add Drive later updates `scopes`
and inserts a capability row on the same row.

---

## 4. The school portal — v0.1

**Versioning (Zhong, 2026-09-21):** v0.1 is what ships for the Sept 30 submission, and it
contains **no browser automation at all**. Every portal-automation idea — scraping,
form-fill, credential handling, session caching — is v0.2, deferred to after the
submission and built only if time allows. §4b holds it. Do not build fragments of v0.2
while working on v0.1; the v0.1 design is complete without them.

The actual problem, stated plainly: **the portal is a maze.** Finding
where a given course's submission page lives takes too many clicks. The student writes
their own work and wants to submit it themselves — they do not want to navigate there.

That is a *navigation* problem, not an authorship or automation problem, and it has a
much cheaper solution than driving a browser: **the maze is static.** A course's
submission path does not change week to week. Solve it once, store the route, and every
later submission is one click from the Dashboard — in the student's own browser, where
they are already logged in. No session on the VPS, no login automation, no irreversible
click owned by the agent.

### `portal_routes`

```sql
CREATE TABLE portal_routes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id  INTEGER NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
    -- Scope: which course, and which kind of action. NULL course_code = a
    -- portal-wide route ("grades", "announcements").
    course_code    TEXT,                       -- "CHEM 2010"
    label          TEXT NOT NULL,              -- "Submit problem set"
    kind           TEXT NOT NULL DEFAULT 'submit'
        CHECK (kind IN ('submit', 'view', 'download')),
    url            TEXT NOT NULL,              -- the deep link, the whole point
    -- Free-text crib for what the page expects: "attach PDF, set Week field,
    -- Submit is bottom-right". Written once, shown next to the link.
    form_notes     TEXT,
    -- Routes rot when the portal is restructured. Surfaced, never trusted.
    last_verified_at TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_portal_routes_course ON portal_routes (course_code);
```

How a route gets created, cheapest first:

1. **The student pastes it.** They are already on the page the one time they submit
   manually; a "save this as CHEM 2010's submission page" field costs nothing and works
   on day one. This alone solves the stated problem.
2. **Athena finds it.** With `web.read` on the portal origin, a crawl of the portal's
   course pages proposes routes for confirmation. Nice, not required — and it degrades
   to (1) if the portal needs a login the agent does not have.

The payoff is a real cross-service moment for Step 7's priority feed: a Calendar deadline
× the topic's `user_understanding` × the stored route becomes *"Problem Set 4 due in 2
days, your Entropy score is 28 — here's the page"* with a working link. That is the
Materials × Calendar × portal routing the judging criteria reward, built from a `TEXT`
column rather than from browser automation.

### Deadlines in v0.1: manual entry via Google Calendar

**Zhong's portal has no iCal feed** (confirmed 2026-09-21), so there is no free deadline
source. For v0.1 the student enters deadlines into Google Calendar themselves — five
minutes of typing per semester, zero portal auth, and it flows through the Calendar sync
being built in Step 5 regardless.

`calendar_events` does not care whether a row came from a scrape or a human. The demo is
identical either way: Athena sees a deadline, cross-references a weak topic, surfaces the
route. Portal scraping in v0.2 *replaces* manual entry; nothing depends on it.

---

## 4b. v0.2 — portal browser automation (deferred)

Everything below is **out of scope for v0.1** and is built only if time remains after the
Sept 30 submission (Zhong, 2026-09-21). It is recorded in full so the reasoning does not
have to be rediscovered, and so v0.1 does not accidentally build half of it.

### What v0.2 adds, and what it does not

v0.1 already gets the student to the right page, in their own browser, signed in. v0.2
adds only: **scraping** (deadlines and assignment listings without manual entry) and
optionally **form-fill up to the submit button**.

It does not add auto-submit. The split to keep, at any automation level: *agent owns the
reversible steps, the human owns the irreversible click.* Submitting coursework is
unrecoverable — wrong file, wrong week, submitted over a better draft — and it is the one
step a student genuinely wants to own.

### Why the cost/benefit is weak

Note what `portal_routes` did to it: the expensive, fragile part of browser automation was
always *finding* the page, and a stored deep link removes that entirely. What remains —
filling a form the student is already looking at — is the part they can do faster
themselves. So v0.2's real value is scraping alone, weighed against:

- **"Logged in" is state on the student's machine, not the VPS.** The VPS is headless and
  has never seen that login. Making it work needs either a persistent profile logged in by
  hand via noVNC, or an agent component on the student's laptop — a second deployment
  target, while Step 11 wants the loop demoed *on the VPS*.
- **Google blocks automated logins.** Playwright/Puppeteer-driven sign-in hits "this
  browser or app may not be secure". If the portal uses Google SSO, the login being
  automated may not be automatable at all.
- **University SSO sessions are short.** Hours, typically. It breaks on a schedule, and
  each repair is another credential handling event.
- **A cached session is broader than the password it replaces.** A live authenticated
  browser profile is an *unscoped* session to the whole Google identity — Gmail, Drive,
  account settings — with no consent screen and no per-scope revocation. This entire table
  exists to request `drive.read` and `calendar.read` and nothing more; a stored profile
  routes around that.

### Rejected: routing the password through Hermes (Zhong, 2026-09-21)

The proposal was a frontend form whose username/password goes backend → Hermes as a chat
message, so nothing is "saved" — Hermes logs in once and the browser cache is kept.

**This stores the password in more places, not fewer.** [`sessions/repository.py:3`] is
explicit that transcripts live in `sessions.payload` as `{"messages": [{role, content,
at}]}`, and `transcript()` replays them into Hermes on every subsequent turn. A password
sent as a chat turn would be:

1. written to `sessions.payload` in **plaintext SQLite**, permanently, by `append_messages`
2. replayed into the model context on every later turn of that session
3. rendered on the Sessions page, which displays the transcript
4. fed into summary generation, so it can be copied into `sessions.summary` as well

An LLM context window is the worst available destination for a secret — it is built to be
logged, replayed, summarized and echoed. And Hermes has no reason to see it: logging in is
not an agentic decision, it is `POST` → Playwright → done.

### The shape to build instead, if v0.2 happens

Zhong's instinct — *don't persist the password* — is right; the Hermes hop is the only
broken part. Keep the form, delete the hop:

```
frontend form → POST /connections/portal/login   (backend, HTTPS only)
              → Playwright login routine; password in a local variable
              → on success: encrypt + store the session cookies in `secret`
              → discard the variable
```

Invariants: the password is never logged, never persisted, never written to `sessions`,
and never enters a prompt. It exists in RAM for the duration of one login. The stored
artifact is a session cookie jar, which expires on its own and is revocable by signing out.

Schema-wise this is `auth_type='browser_session'`, `secret` holding the encrypted cookie
jar or a profile-directory path, under a hard rule: such a connection may hold **no**
capability outside `web.read` / `web.write` scoped to its own `base_url`, each behind its
own toggle. `auth_type='token'` stays reserved for provider-issued revocable tokens — not
for a human's primary password.

Worth stating plainly before building: a university password is typically the same
credential as campus email and SSO, and most acceptable-use policies forbid handing it to
third-party software. That is a policy question for Zhong, not a technical one, and it
should be answered before v0.2 starts rather than after.

---

## 5. Secret handling — the rules that make this table safe

The reason the token wasn't put in `settings`, stated as invariants so they survive
being forgotten:

1. **`secret` is encrypted at rest.** Fernet, key from a new `CONNECTIONS_SECRET_KEY` in
   `.env` alongside the existing `google_client_secret`. Absent key → the connect
   endpoints refuse to start a flow with a readable error, rather than writing plaintext.
2. **`secret` never crosses the repository boundary.** The row → Pydantic mappers omit
   it entirely; there is no `ConnectionOut` field it could leak through. Only
   `get_credentials(slug)` returns it, and only the provider clients call that.
3. **No generic connections CRUD.** No `PATCH /connections/{id}` taking arbitrary
   columns. The write surface is exactly: start-oauth, oauth-callback, set-capability,
   disconnect, and web-allowlist add/remove.
4. **Disconnect revokes, then clears.** Best-effort provider revoke call, then
   `secret = NULL, status = 'disconnected'`. A failed revoke still clears locally, and
   says so.
5. **The `settings` table holds no credentials, ever.** If a future toggle needs one, it
   is a connection.

---

## 6. HTTP surface (`connections/router.py`, prefix `/connections`)

| method | path | purpose |
|---|---|---|
| `GET` | `/connections` | Settings + Knowledge-Sync list: provider, status, account, capabilities, last sync, last error |
| `GET` | `/connections/google/authorize` | → 302 to Google consent (Calendar + Drive scopes, `access_type=offline`) |
| `GET` | `/auth/google/callback` | existing redirect URI from `.env`; exchanges code, upserts the `google` row |
| `PUT` | `/connections/{slug}/capabilities/{capability}` | `{enabled: bool}` — the Settings toggles |
| `DELETE` | `/connections/{slug}` | revoke + clear |
| `POST` | `/connections/web` | `{base_url}` → allowlist entry |
| `DELETE` | `/connections/web/{id}` | remove allowlist entry |

`GET /connections` is the single source for the Settings connector section; the page
stops storing connector state in `settings` entirely.

---

## 7. The one function everything else calls

```python
# app/connections/access.py
def require(slug: str, capability: str) -> Credentials
```

Raises if the connection is missing, `status != 'connected'`, or the capability row is
absent/disabled; refreshes an expired access token in place; otherwise returns decrypted
credentials. Every outbound call — Drive fetch, Calendar sync, Notion read, research-tool
fetch — goes through it.

This is what makes the Settings toggles real rather than decorative, which Step 9's done
condition explicitly demands. A toggle that doesn't gate anything is the failure mode to
design out, so there is exactly one gate and no way around it.

For `provider='web'` the check is by origin rather than slug —
`require_web(url)` resolves the URL's host against the allowlist and refuses on a miss,
including after redirects. A research tool that follows a link off-allowlist is the
obvious hole; the fetch wrapper re-checks every hop.

---

## 8. Build order

**v0.1 — ships for Sept 30.** Steps 1–9. No browser automation, no portal credentials.

1. `004_connections.sql` (incl. `portal_routes`) + `connections/repository.py` +
   `ConnectionOut` schema (no secret field). Testable with a hand-inserted row, no OAuth.
2. Fernet helper + `CONNECTIONS_SECRET_KEY`; round-trip test.
3. `access.require()` + capability gating, unit-tested against fake rows.
4. Google OAuth connect/callback/disconnect. First real provider.
5. `GET /connections` + the Settings connector UI.
6. `005_materials_drive.sql` and the Drive client — now has a token to use.
7. Web allowlist rows + `require_web()`, wired into the research tool.
8. `portal_routes` CRUD + the Dashboard "due soon → here's the page" card. Pure SQLite
   and a link; no auth, no scraping. Can land any time after 1.
9. Notion, last — nothing in the MVP loop depends on it.

Steps 1–3 need no external service and can land immediately.

**v0.2 — only if time remains after submission.** See §4b for the design and its costs.

10. `auth_type='browser_session'` + the `web.write` capability, each behind its own toggle.
11. `POST /connections/portal/login` — Playwright, password in-memory only, encrypted
    cookie jar out. Never touches `sessions`, never touches a prompt.
12. Portal scraping → `calendar_events` with `source='portal'`, replacing manual entry.
13. Form-fill up to the submit button. The student always clicks submit.

Answer the acceptable-use question in §4b before starting 11.

---

## Open items

- ~~**Portal iCal feed**~~ — resolved: there isn't one. v0.1 deadlines are entered by hand
  into Google Calendar (§4).
- **Portal route discovery** — v0.1 ships student-pasted routes only. Athena proposing
  routes by crawling is v0.2, since it needs a login.
- **University acceptable-use policy on credential sharing** — blocks v0.2 step 11, not
  v0.1. Needs Zhong, before any credential-handling code is written.
- **Notion's auth shape** — internal integration token (`auth_type='token'`, paste a
  secret) vs public OAuth app (`auth_type='oauth2'`, needs a registered app). Internal
  token is far less work for a single-user system; confirm before building step 8.
- **Token refresh trigger** — `access.require()` refreshes lazily on use. Whether the
  CRON routine should also refresh proactively (so a 3am sync doesn't fail on a stale
  token) is deferred until the CRON job exists.
- **`connection_capabilities` vs granted scopes drift** — if the user revokes Drive in
  their Google account settings, our `scopes` goes stale and calls start 403ing. Handled
  reactively for MVP: a 403 sets `status='error'` with `last_error`, and the UI prompts
  a reconnect. No proactive scope verification.
