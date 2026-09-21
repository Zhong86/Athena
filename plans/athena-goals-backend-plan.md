# Step 6 — Goal roadmap LangGraph: backend plan

Scope: `raw goal string → clarify → decompose → personalize → approve → committed goal_id`,
implemented as a LangGraph with `interrupt()` at the two human-in-the-loop points. Frontend
is out of scope; every endpoint below is shaped by what `goal-creation.html`,
`goal-detail.html` and `goal-list.html` actually render.

Inherited from the implementation plan (Step 6) and the Notion Backend-Endpoints spec:
the `Milestone` / `RoadmapState` TypedDicts and the node list are **specified literally
and must not be reinterpreted**. This plan only fills in the parts the spec leaves open
(persistence of graph state, HTTP surface, prompt shapes, ranking reuse) and flags where
the mockups ask for fields the spec's TypedDict does not carry.

Decisions locked with Zhong (this session) — see §9 for the reasoning each one settled:
- **Show-all approval only.** No step-through mode; the approval screen renders the whole
  draft immediately, reorder + edit per milestone.
- **`add_milestone` is a first-class action**, and a user-added milestone may be ungrounded.
- **Two reasons per milestone, surfaced as an accordion on both pages.** Collapsed shows
  title + short `reason`; expanded shows `reason_long` plus effort, material provenance and
  prerequisite. This applies to the approval list *and* the committed roadmap on
  goal-detail, where the `current` milestone starts expanded. `goal-detail.html` is not
  followed on this point.
- **Research tool is deferred** — stub the branch, keep the tagging.
- **`Adjust roadmap` is plain CRUD** on committed rows, not a second graph run.

Further decisions this plan assumes:
- **LangGraph with a SQLite checkpointer.** `interrupt()`/`Command(resume=…)` needs a
  checkpointer; the run must survive a page reload, so it cannot be in-memory.
- **No LangChain model wrapper.** Nodes call `agent/hermes.py` directly, same as
  `materials/ingest/tagger.py` does. Hermes is the only LLM in this system and it already
  has a JSON-out prompt convention worth reusing, not re-learning through an adapter.
- **The research tool is a stub for now.** The branch is built and a milestone that cannot
  be grounded is still tagged `source: "research"` — it just carries no fetched content.
  Real research (web fetch gated by `goal.allow_external_sources`, Step 9) lands later and
  only has to fill in the body. Tagging honestly now is what keeps that drop-in cheap;
  forcing a materials link instead would mean unpicking it later.

---

## 0. Dependencies

Add to `backend/requirements.txt`:

```
langgraph
langgraph-checkpoint-sqlite    # SqliteSaver / AsyncSqliteSaver
```

Nothing else. `httpx` (Hermes) and the stdlib `sqlite3` are already in.

Pin both to the versions that resolve at install time and commit the pin, like every
other line in that file. LangGraph's `interrupt()` API is version-sensitive — an
unpinned minor bump is exactly the kind of thing that breaks the demo on the VPS.

---

## 1. Migration `003_goals_roadmap.sql`

`001_initial.sql` already has `goals` and `milestones` with the spec's field names
(`order_index` ↔ `Milestone.order`, `related_topic_ids` as a JSON array). What it lacks:
the goal-level fields the mockups render, and anywhere to keep an in-flight graph run.
All additive.

```sql
-- goals: the fields goal-list.html and goal-detail.html actually render
ALTER TABLE goals ADD COLUMN short_name TEXT;        -- nav label: "Thermo midterm"
ALTER TABLE goals ADD COLUMN course_code TEXT;       -- "CHEM 2010", NULL for career goals
ALTER TABLE goals ADD COLUMN category TEXT NOT NULL DEFAULT 'academic'
    CHECK (category IN ('academic', 'career'));
ALTER TABLE goals ADD COLUMN due_at TEXT;            -- goal deadline, ISO-8601
ALTER TABLE goals ADD COLUMN derivation TEXT;        -- "Derived from your syllabus and…"
ALTER TABLE goals ADD COLUMN order_rationale TEXT;   -- goal-level "why this order" copy
ALTER TABLE goals ADD COLUMN updated_at TEXT;        -- drives "Last updated 2 minutes ago"

-- goals.status: 001 allows draft|committed|archived; the list page also shows "Paused".
-- Widened here rather than in 001 so no existing row or migration is rewritten.
-- (SQLite cannot alter a CHECK constraint, so this is enforced in the repository
-- layer instead -- see note below.)

-- milestones: collapsed row needs `reason`; the expanded accordion needs the rest
ALTER TABLE milestones ADD COLUMN reason_long TEXT;       -- accordion body
ALTER TABLE milestones ADD COLUMN est_effort_min INTEGER; -- "45-60 min" -> 45
ALTER TABLE milestones ADD COLUMN est_effort_max INTEGER; -- "45-60 min" -> 60
ALTER TABLE milestones ADD COLUMN unlocks_after_id INTEGER
    REFERENCES milestones (id) ON DELETE SET NULL;        -- "Unlocks after: <title>"
ALTER TABLE milestones ADD COLUMN progress_status TEXT NOT NULL DEFAULT 'upcoming'
    CHECK (progress_status IN ('upcoming', 'current', 'done'));
ALTER TABLE milestones ADD COLUMN source_chunk_ids TEXT NOT NULL DEFAULT '[]';

-- one row per in-flight creation run; the graph's own state lives in the
-- checkpointer, this table is only the index the HTTP layer looks runs up by
CREATE TABLE roadmap_runs (
    thread_id  TEXT PRIMARY KEY,          -- LangGraph thread id
    goal_id    INTEGER REFERENCES goals (id) ON DELETE CASCADE,  -- NULL until commit
    status     TEXT NOT NULL CHECK (status IN
                   ('clarifying','decomposing','awaiting_approval','committed','abandoned')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
```

Deliberate calls:

- **`est_effort` is split into two integer columns**, not kept as the spec's free-text
  `est_effort: str`. The accordion renders a range with a unit (`45–60 min`); a string
  means the frontend parses model prose to draw it. The `Milestone` TypedDict keeps its
  `est_effort` string field for spec fidelity — the row↔state helper formats it from the
  two columns, so formatting lives in one place.
- **`unlocks_after_id` is a real FK, not prose.** The accordion shows the prerequisite by
  title, so it has to resolve to a milestone; a rejected or reordered prerequisite would
  otherwise leave a dangling name in the copy. `ON DELETE SET NULL` means rejecting a
  milestone quietly clears the pointer instead of orphaning it.
- **`source_chunk_ids` backs two different things.** The accordion's `3 chunks tagged
  "Entropy"` is a count of exactly these ids, and `goal-detail.html`'s focus milestone
  resolves them for its **See related materials** button. Without them both have to re-run
  a vector search and can legitimately return different chunks than the ones the roadmap
  was actually built from.
- ~~**`status` widening is enforced in Python, not SQL.**~~ **Wrong, corrected during
  implementation:** a Python tuple can only *narrow* a CHECK, never widen it — SQLite
  rejects `'paused'` at write time regardless of what the repository allows. Widening means
  rebuilding `goals`, which means dropping it, which with `foreign_keys = ON` cascades every
  milestone row away. So `paused` is **not supported**: `GOAL_STATUSES` is exactly 001's
  three values, `archived` covers "not active", and `PATCH /goals/{id}` rejects `paused`
  with 422. `goal-list.html` styles a Paused pill but never renders one, so nothing on
  screen regresses. If it ever becomes load-bearing it needs a real rebuild migration.

**User-added milestones and the `source` column.** `001` constrains
`source IN ('materials','research')`, and the same no-rewrite rule applies. The column is
nullable and a SQL `CHECK` passes on NULL, so a user-authored milestone with nothing
attached stores `source = NULL`, which the state helper reads back as `"user"`.
`Milestone.source` therefore widens to `Literal["materials","research","user"]` in
`state.py` — one of only two places this plan departs from the spec's TypedDict, and the
departure is additive. If the user *does* attach a topic when adding a milestone, it is an
ordinary `source = 'materials'` row and nothing special happens.

---

## 2. Module layout

Per-feature ownership, same shape as `materials/`:

```
backend/app/
  goals/
    __init__.py
    router.py          # HTTP surface (§6)
    repository.py      # SQL for goals / milestones / roadmap_runs
    schemas.py         # pydantic request/response models
    view.py            # rows -> response models (added during implementation)
    state.py           # Milestone + RoadmapState TypedDicts, row<->state helpers
    graph.py           # StateGraph wiring, checkpointer, conditional edges
    context.py         # materials_context / calendar_context loaders
    llm.py             # JSON-out Hermes calls + sync/async bridge (added)
    research.py        # the research tool (gated fallback path)
    nodes/
      __init__.py
      clarify.py       # clarify_intent
      decompose.py     # decompose_goal
      personalize.py   # personalize_decomposition
      approve.py       # present_for_approval + apply_edits
      commit.py        # commit_roadmap
  ranking.py           # shared weak-topic x deadline ranking (see below)
```

`ranking.py` sits at `app/` top level, not inside `goals/`, because Step 7's Dashboard
Priority Feed calls the same function. The implementation plan is explicit that this must
be **one** function with two callers, not two rankers that agree by coincidence. It is
written here (Step 6 needs it first) and imported by Dashboard later.

Nodes get a package rather than one `nodes.py`: each node is independently testable per
the implementation plan, and `personalize` alone is substantial.

Two modules the plan did not anticipate, both earned during implementation:

- **`llm.py`** — nodes are sync (the checkpointer is sync) and `agent/hermes.py` is async,
  so something has to bridge them. It also owns the JSON-salvage rule. Worth isolating
  because it hides a trap: `HermesError` subclasses `RuntimeError`, so a naive
  "RuntimeError means no event loop, retry" bridge silently re-awaits a spent coroutine on
  every gateway failure.
- **`view.py`** — rows → response models. Out of `router.py` so a goal's shape has one
  definition, out of `repository.py` so SQL stays free of presentation.

---

## 3. State (`goals/state.py`)

`Milestone` and `RoadmapState` go in verbatim from the spec — every field, same names,
same `NotRequired`/`Literal` shape. `RoadmapState` gains nothing at all; the accordion's
extra copy is per-milestone, so it rides on `Milestone` as three additive `NotRequired`
fields. The complete list of departures from the spec's TypedDict, and nothing beyond it:

| Field | Why |
|---|---|
| `source` widened with `"user"` | user-added milestones (§9.2); `NULL` in the row |
| `reason_long: NotRequired[str]` | accordion body (§9.3) |
| `unlocks_after: NotRequired[str]` | prerequisite, by **state id** — the row stores the FK |
| `source_chunk_ids: NotRequired[list[int]]` | must survive the approval interrupt (below) |

`RoadmapState` did gain two control keys during implementation, neither of them copy:
`pending_action` (the resume payload handed from `present_for_approval` to `apply_edits` —
they are separate nodes with a checkpoint between them, and only the node that called
`interrupt()` sees its return value) and `suggested_answers` (parked with the questions they
belong to, for the same reason).

`est_effort` is *not* a departure: it stays the spec's string, formatted from the two
columns at the row boundary.

`source_chunk_ids` has to be *in state*, not recomputed at commit. `personalize_decomposition`
is where the search hits exist; commit happens after an interrupt that may sit for minutes
while the user edits, and re-running the search then can return different chunks — so the
provenance the user approved would not be the provenance stored. Anything a later node
needs and an earlier node learned goes in state; that is what the checkpointer is for.

`Milestone.id` is a **stable string id generated at decomposition time** (spec), while
`milestones.id` is an autoincrement integer. Do not conflate them: the graph reorders and
rejects by the string id long before any row exists. Add
`milestones.state_id TEXT` in §1 if correlating a committed row back to its draft turns
out to matter for `Adjust roadmap`; the simplest version does not need it.

Helpers, both in this module and nowhere else:

```python
def milestone_from_row(row: sqlite3.Row) -> Milestone       # order_index -> order, NULL source -> "user"
def milestone_to_params(m: Milestone, goal_id: int) -> dict # order -> order_index, "user" -> NULL
def format_effort(lo: int | None, hi: int | None) -> str | None   # (45, 60) -> "45-60 min"
```

This module is the **only** place the `order`/`order_index`, `"user"`/`NULL` and
effort-range↔string mappings are allowed to appear. All three are the kind of translation
that, done inline at call sites, ends up done inconsistently at the seventh one.

`unlocks_after` needs both directions handled here too: state holds the prerequisite's
**string** id, the row holds an integer FK, and the mapping between them only exists
during `commit_roadmap`'s insert loop — so commit inserts in `order`, keeps a
`{state_id: row_id}` map as it goes, and patches `unlocks_after_id` in a second pass.
A forward reference (milestone 2 unlocking after milestone 5) must not crash the insert;
it should drop to `NULL`, since it is also nonsense the model should not have produced.

Ids must not come from `uuid4()` inside a node if the graph is ever replayed from a
checkpoint — generate them once in `decompose_goal` and never regenerate.

---

## 4. Context loaders (`goals/context.py`)

Both run **before** `personalize_decomposition` and write into state.

```python
def materials_context() -> dict   # {"topics": [{"id","name","user_understanding","chunk_count"}]}
def calendar_context(days: int = 21) -> dict  # {"deadlines": [{"title","due_at","days_until"}]}
```

- `materials_context` reads `topics` + chunk counts straight from SQLite (no vector
  search — that happens per milestone inside the node). A topic with
  `user_understanding = -1` is **unknown, not weak**; ranking must treat the two
  differently or every fresh install produces a roadmap that claims evidence it lacks.
- `calendar_context` calls Step 5's internal deadline lookup. Step 5 is not built yet,
  so ship this against an empty `calendar_events` table and make the node degrade: no
  deadlines means no reordering and no "due in 3 days" clauses in reasons, not a crash
  and not invented dates.

---

## 5. Nodes

### `clarify_intent` (`nodes/clarify.py`)
- In: `raw_goal_input`, `clarification_turns`.
- **Two passes per round, interrupt first.** A resumed node re-executes from its first
  line, so the pass that collects an answer must open with `interrupt()`. Asking Hermes
  first and interrupting later loses the answer outright: on resume the node re-asks, the
  model now says the goal is clear, and it returns before ever reaching the `interrupt()`
  that would have recorded the reply. So one pass generates questions and parks them in
  state, the conditional edge loops back, and the next pass opens by collecting them.
- One Hermes call, JSON out: `{"needs_clarification": bool, "questions": [...],
  "suggested_answers": [[...]], "clarified_goal": str|null, "extracted":
  {"course_code","due_at","category","short_name"}}`.
- `suggested_answers` exists because the mockup renders quick-reply chips under each
  agent question (`.clarify-option`) — the model must propose them or those chips are
  dead markup.
- If clarification is needed: append questions, `interrupt()` with
  `{"questions": [...], "suggested_answers": [...]}`. The resume payload is
  `{"answers": [...]}` or a single free-text string (the mockup allows both).
- **Max-turns guard (spec-mandated):** `MAX_CLARIFY_TURNS = 3`. On exhaustion, set
  `clarified_goal` to the model's best reading of the transcript and proceed — never loop
  forever, never abandon the run.
- Out: `clarified_goal`, plus the extracted goal fields stashed for `commit_roadmap`.

### `decompose_goal` (`nodes/decompose.py`)
- In: `clarified_goal` only — **no Materials/Calendar context** (spec is explicit that
  personalization is a separate pass; mixing them makes the two steps untestable).
- One Hermes call → ordered `[{title, description, order}]`, 5–8 items (the mockup shows
  7; cap it so the approval list stays reviewable).
- Assigns each draft its stable `id` here. `status: "proposed"`, `source` unset.

### `personalize_decomposition` (`nodes/personalize.py`)
The one node with real logic. Per draft milestone:
1. `search_materials(topic=None, query=<title + description>)` — the existing function,
   imported, not reimplemented.
2. Grounded if the best hit clears a distance threshold: set
   `source: "materials"`, `related_topic_ids`, `source_chunk_ids`, and a `reason` that
   cites the topic's `user_understanding` evidence.
3. Not grounded → **research path** (spec-mandated branch): call `research.py`, set
   `source: "research"`, leave `related_topic_ids` empty. Do not force a materials link
   for a milestone the materials cannot support — that is the failure mode the spec's
   branch exists to prevent.
4. Reorder the whole list via `app.ranking.rank(...)` — weak topic × near deadline. The
   mockup's "moved up to feed the titration lab due in 3 days" copy is this function's
   output made legible, so `rank()` returns `(ordered_items, reasons)`, not just an order.
   A reason nobody can trace back to a signal is the thing to avoid here.
5. Write the accordion fields last, once the order is final: `reason_long`, `est_effort`,
   `unlocks_after`. Both reasons come from **one** Hermes call per milestone returning
   `{"reason": "...", "reason_long": "...", "est_effort_min": 45, "est_effort_max": 60}` —
   two calls would let the short and long copy contradict each other, which is the one
   failure the accordion makes obvious (the user reads both, together, on purpose).
   `unlocks_after` is set from the ranking, not the model: a milestone whose predecessor
   `rank()` placed immediately before it *and* that shares a topic with it gets that
   predecessor's id. A model asked to invent prerequisites will happily invent cycles.
- Out: `draft_milestones` fully populated + ordered, `decomposition_source` =
  `materials` / `research` / `mixed`.

Only steps 1–3 run per milestone with a fresh search; step 5 is one call each and dominates
the node's latency. If a 7-milestone roadmap feels slow on the VPS, batch step 5 the way
`materials/ingest/tagger.py` batches chunks — same prompt shape, same JSON-only rule.

### `present_for_approval` (`nodes/approve.py`)
- On first entry, copy `draft_milestones` → `milestones`.
- `interrupt()` with the **full list, always** — show-all is the only mode. There is no
  per-milestone gating, no `skip`, no cursor through the list, and no partial-approval
  state to track. The clarify transcript sits above it as read-only history (the mockup's
  collapsed steps 1–2), so the interrupt payload carries `clarification_turns` alongside
  the milestones rather than making the client hold them across two requests.
- Resume payload actions: `approve_all`, `reorder(ids_in_order)`,
  `edit(milestone_id, fields)`, `reject(milestone_id)` (the spec's four) plus
  `add_milestone(fields)`.
- **`approve_all` is handled in this node, not in `apply_edits`.** Approval is not an edit,
  and `apply_edits` unconditionally routes back here — so an `approve_all` sent down that
  path could never reach `commit_roadmap`. The conditional edge reads `approval_complete`,
  so the node the edge hangs off has to be the one that sets it.

### `apply_edits` (`nodes/approve.py`)
- Deterministic, **no LLM**. Applies one action, recomputes contiguous `order` values,
  sets `status` to `edited` / `rejected` as appropriate.
- `add_milestone(fields)` accepts `{title, description, position, related_topic_ids?,
  new_topic_name?}`:
  - nothing attached → `source` reads back as `"user"`, `related_topic_ids: []`;
  - existing topics attached → `source: "materials"`, ids stored as given;
  - `new_topic_name` → create the topic via `materials.repository` with
    **`auto_created = 0`** (that flag exists precisely to distinguish "Hermes invented
    this" from "the user named it"), then treat as the previous case.
  - `reason` is set to a fixed `"Added by you."` — not an LLM call. The user just told us
    why; asking a model to narrate their intent back to them is worse than saying nothing.
- Always routes back to `present_for_approval` — no silent-acceptance path (spec).

### `commit_roadmap` (`nodes/commit.py`)
- Insert the goal (title/short_name/course_code/due_at/category/derivation/
  order_rationale from clarify + personalize), then the non-rejected milestones in order,
  in **one transaction**: a goal with half a roadmap is worse than no goal.
- First milestone gets `progress_status: 'current'`, rest `upcoming`.
- Sets `final_roadmap`, `status: "committed"`, updates `roadmap_runs`, returns `goal_id`.

### Conditional edges (`graph.py`)
Exactly the spec's two:
- after `clarify_intent`: → `decompose_goal` if `clarified_goal` set, else → `clarify_intent`
- after `present_for_approval`: → `commit_roadmap` if `approval_complete`, else → `apply_edits`

Plus one node the spec implies without naming: **`load_context`**, sitting between
`decompose_goal` and `personalize_decomposition`. The spec has `materials_context` and
`calendar_context` in state without saying who fills them, and doing it in its own node
keeps `personalize_decomposition` a pure function of its inputs — which is what makes it
testable without a database. It runs *after* decomposition, never before, because the spec
is explicit that `decompose_goal` must not see either signal.

The checkpointer gets **its own SQLite file** (`data/roadmap_checkpoints.db`), not a table
in the app database: LangGraph owns that schema and migrating it is not our business.

---

## 6. HTTP surface (`goals/router.py`)

Prefix `/goals`. The graph is driven by three endpoints; everything else is plain reads.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/goals/roadmap` | start a run: `{raw_goal_input}` → `{thread_id, status, interrupt}` |
| `POST` | `/goals/roadmap/{thread_id}/resume` | `Command(resume=payload)` → next `interrupt` or `{goal_id}` |
| `GET` | `/goals/roadmap/{thread_id}` | current state, for reload/"Save and exit" recovery |
| `DELETE` | `/goals/roadmap/{thread_id}` | abandon a draft run |
| `GET` | `/goals` | goal-list cards: id, title, short_name, status, category, percent, sub-line parts |
| `GET` | `/goals/{id}` | goal-detail: goal fields + **fully-detailed** ordered milestones + topic strengths |
| `PATCH` | `/goals/{id}` | goal-level edit (title, status, due_at) |
| `POST` | `/goals/{id}/milestones` | add one post-commit |
| `PATCH` | `/goals/{id}/milestones/{mid}` | edit title/description/progress_status |
| `DELETE` | `/goals/{id}/milestones/{mid}` | remove one |
| `PUT` | `/goals/{id}/milestones/order` | `{ids_in_order}` → renumber |

**`GET /goals/{id}` returns the same milestone payload the approval screen gets** — the
detail page's roadmap items are accordions too, and the `current` one renders **expanded
by default** rather than collapsed to its reason. This is a deliberate divergence from
`goal-detail.html`, which shows reason-only items; the mockup is not binding here.

Two consequences for the response shape, both of which would be wrong under a
reason-only detail page:

- No trimmed milestone DTO. One `MilestoneOut` serves creation and detail, carrying
  `reason`, `reason_long`, `est_effort`, `unlocks_after_title`, `progress_status` and
  `source_chunks`. A second, thinner shape would have to be widened the first time any
  item expands — which is now every item.
- `source_chunks` is **hydrated, not raw ids**: `[{chunk_id, topic_name,
  source_filename}]`, resolved in one `materials.repository.get_chunks(conn, ids)` call
  across the whole goal. That single call serves both the accordion's `N chunks tagged
  "Topic"` line and the focus milestone's **See related materials** action, so neither
  needs its own endpoint and neither re-runs a vector search.

Because the focus milestone is expanded on arrival, its content is on the critical path
for the page — anything lazy-loaded per-expansion would be visible as a flash on load.
Hydrate it in the same response.

**`Adjust roadmap` is the bottom five rows, not the graph.** Re-entering
`goal-creation.html` from a committed goal does plain CRUD on `milestones`: no new
`thread_id`, no re-clarify, no re-decompose, no checkpointer involvement. This is the
single biggest scope saving in the step — a graph run seeded from committed rows would
need the whole state round-trip built backwards. The cost is that post-commit edits get no
fresh reasons or reordering; a user who wants those creates a new goal.

`PUT .../order` renumbers in one transaction rather than accepting N `PATCH`es, because a
half-applied reorder leaves two milestones sharing an `order_index` and the detail page
renders them in arbitrary order.

Every graph endpoint returns the same envelope so the frontend has one code path:

```json
{"thread_id": "...", "status": "awaiting_approval",
 "interrupt": {"kind": "clarify"|"approval", "payload": {...}},
 "goal_id": null}
```

`interrupt: null` + a `goal_id` means committed. Graph invocation is **sync LangGraph in
a worker thread** (`anyio.to_thread.run_sync`) unless the async checkpointer proves
painless — the rest of the backend already uses sync `sqlite3` inside async routes, and
one concurrency model beats two. Progress percent is computed
(`done / total`), never stored: a stored percent drifts the first time a milestone is
edited.

---

## 7. Tests

Hermes is stubbed in every test — `tests/test_hermes.py`'s existing pattern. No test may
depend on a live gateway or a live model download.

- `test_goal_state.py` — row↔state round-trip in both directions: `order` ↔ `order_index`,
  `source = NULL` ↔ `"user"`, `(45, 60)` ↔ `"45-60 min"`, and `unlocks_after` string id ↔
  integer FK (including a forward reference falling back to `NULL`).
- `test_ranking.py` — weak-topic × deadline ordering; `user_understanding = -1` is not
  treated as weak; empty calendar is a no-op.
- `test_goal_nodes.py` — per node, stubbed Hermes: clarify loops then exits on the
  max-turns guard; decompose assigns stable ids; personalize tags `materials` vs
  `research` correctly when `search_materials` returns hits vs nothing; `apply_edits`
  renumbers `order` contiguously after a reject, and `add_milestone` with no topics
  produces a `"user"`-sourced milestone while `new_topic_name` creates a topic with
  `auto_created = 0`.
- `test_goal_graph.py` — full round-trip against an in-memory checkpointer: goal string in
  → clarify Q&A → draft list → reorder → edit → approve_all → `goal_id`, with milestone
  rows persisted in the committed order. Asserts `source_chunk_ids` on the committed rows
  match what `personalize` saw, with the stubbed search returning *different* hits on a
  second call — that is the regression the in-state decision exists to prevent.
- `test_goals_router.py` — the envelope shape, resume with an unknown `thread_id` → 404,
  resume after commit → 409 rather than a second goal.
- `test_goals_crud.py` — the post-commit path: reorder via `PUT .../order` leaves no
  duplicate `order_index`, deleting the `current` milestone promotes the next one, and
  none of it touches the checkpointer.

---

## 8. Status — built

Implemented and passing. Migration `003_goals_roadmap` applied cleanly to the dev database;
`/health` reports `schema_version: 003_goals_roadmap`, `pending_migrations: 0`. With Hermes
down, `POST /goals/roadmap` returns 503 with the gateway error and creates no goal.

Not yet exercised against the real Hermes gateway at `103.30.146.148:8642` — it has been
unreachable throughout. The full flow *has* been driven end-to-end in a browser against a
stand-in gateway speaking the `api_server` shapes, which is what §10 records.

## 8b. Done when

A full round-trip works end-to-end with a stubbed Hermes **and** once by hand against the
real gateway: raw goal string in → clarifying Q&A → draft milestones with correct
`source` tagging and traceable reasons → reorder/edit/reject via resume payloads →
committed goal with persisted milestones and a returned `goal_id` → `GET /goals/{id}`
returns every field an expanded accordion row needs, for every milestone, with
`source_chunks` hydrated and the `current` milestone complete enough to render expanded
without a second request. `app.ranking.rank` has exactly one implementation, and Step 7
will import it rather than copy it.

Where this plan diverges from the mockups, it says so and why: the cut step-through mode
(§9.1), and accordion roadmap items on goal-detail (§9.3). Everything else still treats
the built HTML as the contract.

---

## 9. Resolved decisions

Answered by Zhong; recorded because each one deletes work the mockups would otherwise imply.

1. **Show-all only.** The `Approve one by one` toggle, `Skip for now`, and per-milestone
   `Approve and continue` are cut from the mockup, not from the backend — there was never
   backend for them. Consequences: no `skip`/`approve_one` resume actions, no
   per-milestone approval tracking, and the whole draft renders at once.
2. **`add_milestone` allows an ungrounded milestone.** The user may attach nothing, pick
   existing topics, or name a new one. Nothing → `source` is `"user"` (`NULL` in the row);
   see §1 and the `apply_edits` spec in §5.
3. **Two reasons, surfaced as an accordion.** The step-through panel is cut, but its
   content is not: each milestone row collapses to title + short `reason` and expands to
   `reason_long` + estimated time + `N chunks tagged "Topic"` + `Unlocks after`. So the
   fields the panel used all survive (§1), they just move into the show-all list. Both
   reasons are generated in one call so they cannot contradict each other (§5).
   The same accordion applies on goal-detail, with the `current` milestone **expanded on
   arrival** — so `GET /goals/{id}` returns full detail for every milestone, not a trimmed
   list (§6). `goal-detail.html` renders reason-only items and is knowingly not followed.
   Unaffected: `goals.order_rationale` is the separate goal-level "why this order" copy,
   and Dashboard's priority-feed copy is generated in Step 7 from `app.ranking`.
4. **Research tool deferred.** Branch and tagging built now, fetching later. See the
   decision note at the top.
5. **`Adjust roadmap` is plain CRUD.** No second graph run — see the note under §6.

Nothing is blocking. Build order: §1 migration → §3 state → `ranking.py` → nodes in
§5 order → §6 router.

---

## 10. Frontend wiring — built

Pages: `/goal` (list) · `/goal/new` (the graph-driven creation flow) · `/goal/[id]` (detail).
One CSS module, `app/goal/goal.module.css`, ported from `goal-list.html`, `goal-detail.html`
and `goal-creation.html`. Types and fetchers for every endpoint above live in `lib/api.ts`
beside the Materials ones.

- **The flow keeps its thread id in the URL** (`/goal/new?thread=…`). That is what makes
  "Save and exit" survivable: reopening the link calls `GET /goals/roadmap/{thread_id}`,
  which rebuilds the parked interrupt without advancing the graph.
- **The approval screen sends one action per resume and re-renders from the interrupt that
  comes back.** No client-side copy of the milestone list is kept — `apply_edits` always
  routes back to `present_for_approval`, so the interrupt payload is the only state.
- **Reorder is two buttons, not the mockup's drag handle.** `⠿` in the mockup is decorative;
  a keyboard-reachable pair sends the same `reorder(ids_in_order)` action.
- **`Adjust roadmap` is a mode on the detail page**, not a link back into the creation flow
  (§9.5): it reveals move/edit/remove per row plus an add row, all plain CRUD.
- **Rejecting during approval asks first**, because the graph has no un-reject action — a
  rejected milestone stays struck through until commit drops it.

Three things the UI forced back into the backend, each with a test:

1. **`GET /goals/roadmap`** (`list_unfinished_runs`). Without it "Save and exit" was a
   trapdoor: the thread id only ever lived in that page's URL, so closing the tab made a
   parked interrupt unreachable. The list page now shows unfinished runs as draft rows.
2. **`raw_goal_input` on `RoadmapEnvelope`.** Neither interrupt payload carries the
   student's opening sentence, so a reopened draft could not redraw the top of its own
   thread.
3. **The focus invariant on `update_milestone`.** `delete_milestone` already promoted a
   successor; a progress change did not, so marking the focus done left a goal with no
   `current` milestone, and promoting another left two. Both are visible on the detail page
   as a missing or duplicated "Focus now" pill. Fixed in `repository._settle_focus`.

Verified in the browser, against a stand-in gateway: a clarifying round with quick-reply
chips → the approval list → a reorder that renumbered through the graph → commit → the
detail page with the focus milestone expanded on arrival, its short reason collapsed into
the row, `45–60 min`, `2 chunks tagged “Entropy” and “Heat transfer”`, and weak/strong topic
chips from real scores. Then, post-commit: mark done (percent 0 → 25, focus promoted to the
next stage), add, remove, and reopen-a-saved-draft. No horizontal overflow at 375px.

Not built: the research-note banner has no ungrounded milestone to render yet (the research
tool is still the §9.4 stub), and `calendar_context` is empty until Step 5, so
`order_rationale` currently cites check-ins only and never a deadline count.
