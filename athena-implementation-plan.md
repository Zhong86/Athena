# Αθηνα (Athena) — Implementation Plan for a Coding Agent

Source of truth: Notion pages under "Hermes - Hackathon Agent" (project hub, App Flow, Backend Endpoints/LangGraph design, Submission) plus the built HTML mockups at `/mnt/project/`. This plan sequences implementation into steps a coding agent can execute one at a time, each with a concrete "done" condition.

**Competition constraints that shape the plan:** IDwebhost + PANDI AI HackFest 2026, single 5-day VPS batch, submission is two public links (demo video + article) due Sept 30 — no source code upload. This means the plan should front-load anything that needs to be *seen working on the VPS* on camera, and treat polish/deferred items as truly optional.

---

## 0. Ground rules for the coding agent

- Single-user system. No multi-tenancy, no per-student session routing. One fixed `X-Hermes-Session-Key` held server-side alongside Hermes' `API_SERVER_KEY`.
- Stack: Next.js (frontend) · FastAPI (Python, backend) · LanceDB (chunk embeddings only) · SQLite (everything relational: goals, milestones, settings, session/quiz history) · `bge-small-en-v1.5` or `bge-base-en-v1.5` (embedding model) · Hermes Agent framework (Nous Research) via its `api_server` adapter.
- External connectors: Google OAuth, Google Drive access. No Canvas/LMS.
- Chunk-first materials model: files → chunks → chunks tagged to topics (MVP: one chunk = one topic, LLM-classified). Topic pages own chunks, not files. A topic's material list is the chunks tagged to it; the topic page shows the distinct source files behind those chunks.
- Match existing design system exactly for every new page: Fraunces (display) + Inter (UI), CSS vars `--bg --surface --ink --ink-soft --line --indigo --indigo-soft --coral --coral-soft --sage --sage-soft`, sticky "Αθηνα" chat FAB + panel, D2 desktop sub-nav (back arrow replaces top nav) / M1 mobile (full-screen list → detail).
- Every feature must visibly derive from cross-service signals (Materials × Calendar, Materials × Goal, etc.) — a feature that doesn't need this routing is an architectural weakness for this hackathon's judging criteria and should be cut or reframed.

---

## Step 1 — Repo & environment scaffolding

**Goal:** a running skeleton, nothing functional yet.

- Create monorepo structure: `/frontend` (Next.js), `/backend` (FastAPI), `/backend/agent` (Hermes integration).
- `backend`: FastAPI app skeleton with `/health`, SQLite file + connection setup, `.env` for `API_SERVER_KEY`, `X-HERMES_SESSION_KEY`, Google OAuth client id/secret.
- `frontend`: Next.js app, port config, `.env.local` for backend base URL.
- Wire up Hermes `api_server` adapter per https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server — confirm request/response shape before building on top of it.
- **Done when:** `GET /health` returns 200, Next.js dev server renders a blank page, Hermes adapter responds to a trivial prompt round-trip.

## Step 2 — SQLite schema

**Goal:** all relational tables from the spec, migratable.

Tables (minimum):
- `topics` (id, name, description, user_understanding score default -1)
- `chunks` (id, source_file_id, topic_id, text, embedding_ref → LanceDB row id)
- `source_files` (id, filename, upload_type: text/pdf/image, uploaded_at)
- `sessions` (id, type: chat/quiz/cron/agent_action, started_at, payload/summary)
- `quiz_attempts` (id, session_id, topic_id, question, answer, correct, timestamp)
- `goals` (id, title, description, status, created_at)
- `milestones` (id, goal_id, title, description, order, status: proposed/approved/edited/rejected, reason, source: materials/research, related_topic_ids, est_effort)
- `settings` (key, value) — flat key/value, scoped by the page-ownership convention (materials.*, goal.*, general.*)
- `calendar_events` (id, source: google/portal, title, due_at, raw_payload) — backend-only, no CRUD UI

**Done when:** migrations run clean, schema matches Milestone/RoadmapState TypedDicts from the Backend Endpoints spec exactly (field names must match — the LangGraph state schema in Step 6 depends on this).

## Step 3 — Materials ingestion pipeline

**Goal:** upload → chunk → tag → embed → retrievable.

1. Upload endpoint accepts text, PDF, image (MVP formats only — no audio/video).
2. Chunking: break uploaded text into chunks (reasonable size, e.g. paragraph or fixed-token window).
3. LLM-based topic tagging: each chunk gets classified into exactly one topic (create topic if none fits — confirm creation policy with Zhong if ambiguous, otherwise auto-create).
4. Embed each chunk with `bge-small-en-v1.5` (or `-base-` variant), store vector in LanceDB, store `embedding_ref` back on the chunk row in SQLite.
5. Implement `search_materials(topic, query)` as a Hermes tool — this is the exact signature named in the spec, used both by quiz generation and by the Goal roadmap's `personalize_decomposition` node later.

**Done when:** a test file uploads, produces chunks tagged to topics, and `search_materials("thermodynamics", "entropy")` returns relevant chunks.

## Step 4 — Topic understanding score + quiz loop

**Goal:** `user_understanding` score computed and updated, quizzes generated from materials.

1. Quiz generation: agent produces open-ended questions from a topic's chunks (via `search_materials`).
2. Quiz attempt scoring updates the topic's `user_understanding` score (default -1 = unsure until first signal).
3. Chat-session-derived scoring: sessions where a user asks the agent to re-explain a concept, or answers questions in-thread, also feed the score (per Settings: "Allow scoring from chat sessions" toggle) — this is the "confidence updates after every quiz and every session" behavior shown in `materials.html`.
4. Every score change must log its evidence (which quiz, which session) so the Materials UI can show "why this score" — this evidence trail is already designed into `topic-detail.html` / `materials.html` and must be backed by real data, not decorative copy.

**Done when:** a quiz round-trip changes a topic's score and the evidence is queryable (which quiz/session caused it, with timestamp).

## Step 5 — Google Calendar integration

1. Google OAuth flow, Calendar scope only.
2. Sync job (CRON, per Settings spec) pulls upcoming events into `calendar_events`.
3. Expose an internal function (not a user-facing endpoint) that returns "deadlines within N days" for use by Dashboard ranking and the Goal roadmap's `personalize_decomposition` node.

**Done when:** connecting a real Google account populates `calendar_events`, and the internal deadline-lookup function returns correct results.

## Step 6 — Goal roadmap LangGraph (the centerpiece — build exactly per the Backend Endpoints spec)

This is the most fully-specified part of the Notion spec and should be implemented literally, not reinterpreted. Build the graph in this order so each node is independently testable:

1. **State schema** — implement `Milestone` and `RoadmapState` TypedDicts exactly as specified (see field list in Step 2). Do this first; every node below reads/writes this shape.
2. **`clarify_intent`** — takes `raw_goal_input`; if ambiguous (no timeframe, no clear scope), generates `clarifying_questions` and calls `interrupt()`. Loops on itself bounded by a max-turns guard. Outputs `clarified_goal`.
3. **`decompose_goal`** — LLM call, `clarified_goal` → ordered list of draft milestones (title + description + rough order), no Materials/Calendar context yet.
4. **`personalize_decomposition`** — for each draft milestone: attempt to ground it against Materials topics via `search_materials`/topic strength scores; if no correlation exists, invoke the research tool for that milestone instead (tag `source: "materials"` vs `"research"`). Reorder using the same weak-topic-near-deadline logic already implemented for Dashboard's Priority Feed — do not build a second ranking system. Pull `materials_context` and `calendar_context` into state before this node runs.
5. **`present_for_approval`** — copies `draft_milestones` → `milestones` on first entry, `interrupt()`s with the full list ("show all" pattern from `goal-creation.html`). Resume payload actions: `approve_all`, `reorder(ids_in_order)`, `edit(milestone_id, fields)`, `reject(milestone_id)`.
6. **`apply_edits`** — deterministic, non-LLM. Applies the resume action to `milestones`, recomputes `order`. Always routes back to `present_for_approval` (no silent-acceptance path).
7. **`commit_roadmap`** — persists `milestones` to SQLite (`goals`/`milestones` tables), sets `final_roadmap` and `status: "committed"`, returns `goal_id`.
8. **Conditional edges** — implement exactly as specified:
   - after clarify: `decompose_goal` if `clarified_goal` set, else loop `clarify_intent`
   - after approval: `commit_roadmap` if `approval_complete`, else `apply_edits`

**Done when:** a full round-trip — raw goal string in, clarifying Q&A if needed, draft milestones with correct `source` tagging, edit/reorder via resume payload, committed goal with persisted milestones and returned `goal_id` — works end-to-end and matches the `goal-creation.html` → `goal-detail.html` UI flow already built.

## Step 7 — Dashboard priority feed (reuses Step 6's ranking logic)

**Goal:** live version of `dashboard.html`.

1. Priority Feed: merge `calendar_events` deadlines with topic `user_understanding` scores using the same weak-topic-near-deadline ranking used in `personalize_decomposition` (Step 6.4) — implement this ranking as one shared function, called from both places.
2. Last Check-in card: surface the most recent quiz/session-driven score change with its evidence (from Step 4).
3. Weak Topic Alert: topics currently below a threshold score.
4. Cold-start variant: if no quiz/session history exists yet, render the Setup Progress / explanatory empty-state version already mocked up, not an empty container.

**Done when:** Dashboard reflects real data, and the same ranking function used here is the one Step 6 calls (not a duplicate).

## Step 8 — Sessions log

**Goal:** live version of `sessions.html`.

1. Unified chronological log of chats, quizzes, cron results, and agent actions.
2. Filter bar: All / Chats / Quizzes / Findings / Agent actions.
3. Separate, standalone agent memory list (facts about the student derived from behavior — distinct from session history per the locked architectural decision). 
**Done when:** real sessions/quizzes appear in the timeline with correct type coloring and filtering; memory list is backed by whatever data source Zhong confirms.

## Step 9 — Settings

**Goal:** live version of `settings.html`, per-page toggle ownership.

- Materials: allow LLM auto-embedding, allow scoring from chat sessions, auto re-quiz on decay.
- Goal: allow external sources (research tool), milestone auto-reorder without approval.
- General: Google Calendar access toggle, Calendar write-access sub-toggle (nested), websites to access, CRON routine schedule.
- Explicitly out of scope for MVP: agent tone UI, full memory management, Google Drive storage, audio/video transcription — do not build UI for these.

**Done when:** every toggle reads/writes real values in the `settings` table and actually gates the corresponding backend behavior (e.g. turning off "scoring from chat sessions" actually stops Step 4.3 from firing).

## Step 10 — Nav cleanup pass

- Confirm Calendar is removed from the nav on every page (Dashboard, Goal, Materials, Sessions, Settings) — this was flagged as possibly incomplete in the mockups.
- Confirm sub-nav behavior matches the locked spec: desktop D2 (back arrow replaces top nav on drill-in), mobile M1 (full-screen list → detail with back arrow).

**Done when:** no page references Calendar as a clickable nav item; drill-in behavior is consistent across Goal/Materials/Sessions.

## Step 11 — End-to-end pass on VPS (needed for the demo video)

- Deploy backend + frontend to the assigned VPS.
- Walk the full loop live: upload material → get a topic score → see it on Dashboard → create a goal → watch the roadmap get personalized against that weak topic and an upcoming Calendar deadline → approve/edit milestones → commit → see it reflected on Goal page.
- Capture one clear on-camera moment showing the VPS dashboard or terminal (submission requirement).

**Done when:** the above loop works without manual DB edits, on the actual VPS, in one sitting — this is your demo video's spine.

