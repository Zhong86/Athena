# Athena — Feature Summary & Hermes Skills Plan

Grounded in the current codebase (2026-09-21). Purpose: give enough surface area to design Hermes skills/routines on the VPS that plug into Athena's existing API.

Backend talks to Hermes via [backend/agent/hermes.py](../backend/agent/hermes.py), which exposes:
- `ping()` — GET `/health`, unauthenticated liveness check.
- `chat(messages, session_id, system)` / `complete(prompt, system)` — POST `/v1/chat/completions`, synchronous, full transcript each call, no tool use surfaced.
- `run(prompt, session_id, instructions)` — POST `/v1/runs` + SSE event drain into `RunResult{run_id, status, output, trace}`. **Not called anywhere in production code today** — only exercised in tests. This is the intended hook for trace-visible background/agent work and currently has no caller.

---

## 1. Dashboard

`GET /dashboard` — cold-start flag, latest check-in, weak-topic list, priority feed ranked by `app/ranking.py`. Ranking is a pure function of `user_understanding` (-1..100), not deadline-aware. Bands: unknown (<0), weak (<40), fair (<70), strong (≥70).

**Hermes usage:** none — pure read/aggregation.

## 2. Goals (LangGraph roadmap builder)

Endpoints: `POST /goals/roadmap` (start), `POST /goals/roadmap/{id}/resume` (answer interrupt), `GET /goals/roadmap` (list drafts), `GET /goals/roadmap/{id}` (peek), `DELETE /goals/roadmap/{id}` (abandon), `GET /goals`, `GET /goals/{id}`, `PATCH /goals/{id}`, plus milestone CRUD (`POST`/`PATCH`/`DELETE`/`PUT .../order`).

Graph nodes (`backend/app/goals/graph.py`, LangGraph + SqliteSaver checkpoint):
1. **clarify_intent** — up to 3 rounds of ≤2 clarifying questions via `interrupt()`; falls back to raw student wording if Hermes is unreachable (never blocks).
2. **load_context** — pulls topics + `user_understanding` + chunk counts, deliberately after decomposition.
3. **decompose_goal** — one Hermes call → 3–8 draft milestones. No fallback; Hermes failure 503s the run.
4. **personalize_decomposition** — vector-searches Materials per milestone (`search_materials`); tags `source="materials"` with provenance if a hit is found within distance 1.1, else routes to the **research branch (currently a stub)** tagged `source="research"`. Reorders via the same ranking function as Dashboard. One more Hermes call per milestone for reason/effort copy, grounded only in real evidence.
5. **present_for_approval / apply_edits** — show-all approval only; resume actions `approve_all`, `reorder`, `edit`, `reject`, `add_milestone`. `apply_edits` is deterministic, no LLM call.
6. **commit_roadmap** — persists goal + milestones in one transaction.

**Research tool status:** [backend/app/goals/research.py](../backend/app/goals/research.py) `investigate()` returns fixed "not covered" copy, `available()` is `False`. No web access implemented — plan calls for real access restricted to a user-configured allowlist.

**Hermes usage:** `goals/llm.py:ask_json()` wraps `hermes.complete()` for clarify/decompose/personalize-copy. All synchronous JSON prompts, no `run()`.

## 3. Materials (ingest + search)

Endpoints: `POST /materials/uploads` (file), `POST /materials/uploads/text` (paste), `GET /materials/uploads` (list), `GET /materials/uploads/{id}` (poll status), `POST /materials/uploads/{id}/retry`, `DELETE /materials/uploads/{id}`, `GET /materials/topics`, `GET /materials/topics/{id}`, `GET /materials/topics/{id}/chunks`, `POST /materials/search`.

Ingest pipeline (`materials/ingest/pipeline.py`, background task, never raises — failures recorded on the row):
1. **Extract** — UTF-8 text, PDF via `pypdf` (rejects scanned/password-protected PDFs, no OCR fallback), images via `pytesseract` OCR (deliberately not a vision model — OCR failure is visible garbage; a hallucinated vision read isn't safe to quiz against).
2. **Chunk** — pure/deterministic, paragraph-first, 1000-char ceiling, 150-char overlap, falls back to sentence- then hard character-splitting.
3. **Tag** — batches of 20 chunks, one Hermes call per batch (sequential), classifies into existing/new topic, caps new topics at 8/ingest with fuzzy-match fallback. Non-fatal if Hermes is down (chunks stay untagged but embedded/searchable).
4. **Embed** — local `fastembed`/bge-small (**not** Hermes), CPU-bound off the event loop. SQLite is source of truth; LanceDB holds vectors only, explicitly rebuildable.

`search_materials(topic, query)` is the one search function, reused by goal personalization and (per plan) intended for quiz generation.

**Not yet built:** topic digests (`summary`/`key_concepts`), Google Drive origin (schema fields exist, no OAuth/fetch code).

**Hermes usage:** tagging only (`hermes.complete`). Embedding is local. No `run()`.

## 4. Quizzes

Endpoints: `GET /quizzes`, `GET /quizzes/evidence/{topic_id}`, `GET /quizzes/{id}`, `POST /quizzes`, `DELETE /quizzes/{id}`, `POST /quizzes/{id}/answers`, `POST /quizzes/{id}/submit`.

**`POST /quizzes` accepts an already-generated quiz — generation itself is explicitly not in this backend.** The router docstring says generation happens "elsewhere," matching the plan's intent: an agent produces open-ended questions from a topic's chunks via `search_materials`, then calls back into this endpoint. **This is an unbuilt Hermes skill.**

Grading (`quizzes/grading.py`): MC is deterministic arithmetic. Open-ended is one Hermes call per answer, graded against the *exact resources the quiz was generated from* (not a fresh search), so grading can't drift from generation. If open-ended grading fails partway, the whole quiz stays ungraded rather than scoring on the easier MC half alone.

Scoring (`quizzes/scoring.py`): topic `user_understanding` = 40% previous + 60% new quiz score (first signal takes the raw score). Every change writes a `reason` + structured `evidence`.

**Hermes usage:** open-ended grading only (`hermes.complete`, one call/answer). Generation is external — a Hermes-driven flow calling `POST /quizzes`.

## 5. Sessions

Endpoints: `GET /sessions` (filter by `type`/archived), `POST /sessions`, `GET /sessions/{id}`, `PATCH /sessions/{id}` (rename/archive), `DELETE /sessions/{id}`, `POST /sessions/{id}/chat` — the one live chat endpoint (persists user msg, calls `hermes.chat()` with a fixed "Athena, a study agent" system prompt, persists reply; user message isn't persisted if the Hermes call fails, so retries don't duplicate).

`sessions.type` ∈ `chat`, `quiz`, `cron`, `agent_action`. **Only `chat` and `quiz` are created by backend code today.** `cron` and `agent_action` are defined in the schema and rendered on the frontend Knowledge-Sync page, but **nothing creates them.** This is the clearest hook: a scheduled Hermes routine (or an autonomous agent action) should `POST /sessions` with `type="cron"`/`"agent_action"` — likely via `hermes.run()`, to get a trace — to populate this feed.

**Hermes usage:** `chat()` only, full transcript, synchronous. No `run()`.

## Frontend cross-reference

| Page | Backed by | Notes |
|---|---|---|
| `/dashboard` | `GET /dashboard`, `hermes.ping()` | shows Hermes up/down |
| `/goal`, `/goal/new`, `/goal/[id]` | goals endpoints | wizard keeps thread id in URL for resumability |
| `/materials`, `/materials/[id]` | materials endpoints | upload + topic/chunk viewer |
| `/quizzes`, `/quizzes/[id]` | quizzes endpoints | take/review UI |
| `/sessions`, `/sessions/[id]` | sessions endpoints | **chat only** by design |
| `/knowledge-sync`, `/knowledge-sync/[id]` | sessions `type=cron`/`agent_action` | "what Athena did on its own" — **always empty today**, nothing populates these types |
| `/settings` | localStorage only | explicit "no settings endpoint yet" comment; designed-but-unbuilt: auto-embed, score-from-chat, auto-requiz-on-decay, research allowlist, Drive connect, cron schedule control |

Plans (`plans/`) additionally describe an unbuilt `connections` module (Drive/Notion OAuth, allowlisted research access, school-portal deep links) — no `connections` directory exists in `backend/app` yet.

---

## Skills to build on the VPS

Ranked by how directly the existing API already expects them:

1. **Quiz generator** — reads a topic via `search_materials`/`GET /materials/topics/{id}/chunks`, drafts open-ended + MC questions, `POST /quizzes`. This is the most concretely-specified gap: the endpoint contract already exists and its docstring says generation happens externally.
2. **Cron/Knowledge-Sync routine** — a scheduled skill that reviews weak topics (`GET /dashboard`) or stale goals and does something useful (draft a quiz, flag a milestone, nudge on a due date), then `POST /sessions` with `type="cron"` and a trace summary. This is what the Knowledge-Sync page is built to display and currently never receives.
3. **Research tool** — replace the stub in `goals/research.py`: fetch from a user-configured allowlist of sites when a milestone has no Materials coverage, return grounded snippets the same shape as a materials hit (`source="research"`).
4. **Materials tagger / topic digest** — optional follow-on: summarize a topic (`summary`/`key_concepts`) cheaply so an agent can answer "what is this student studying" without a vector search — designed in `athena-materials-backend-plan.md`, not yet built anywhere (backend or skill).

All four are additive — none require changing existing endpoint contracts, since `POST /quizzes` and `POST /sessions` already accept exactly this shape of input from an external caller.
