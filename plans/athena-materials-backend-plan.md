# Step 3 — Materials ingestion: backend plan

Scope: `upload → extract → chunk → tag → embed → retrievable`, ending at a working
`search_materials(topic, query)`. Frontend is out of scope; every endpoint below is
shaped by what `topic-detail.html` actually renders.

Decisions already locked (Zhong, this session):
- Embeddings: **fastembed** (ONNX `BAAI/bge-small-en-v1.5`, 384-dim, no torch).
- Images: **tesseract OCR** via `pytesseract`.
- Ingestion: **background task + status polling** (POST returns 202).

---

## 0. Dependencies

Add to `backend/requirements.txt`:

```
python-multipart      # FastAPI multipart/form-data — required for UploadFile
lancedb
pyarrow               # lancedb schema definitions
fastembed
pypdf                 # PDF text extraction, pure python
pytesseract           # OCR wrapper
pillow                # pytesseract needs PIL.Image
```

VPS prerequisite: `apt-get install -y tesseract-ocr`. The pipeline must degrade to
`ingest_status='failed'` with a readable `ingest_error` if the binary is missing —
never crash the request.

First fastembed call downloads ~130MB to a cache dir. Warm it in `lifespan` (fire the
model constructor, not a full encode) so the first upload isn't paying for it, and pin
the cache under `backend/data/models/` via a new `Settings.fastembed_cache`.

---

## 1. Migration `002_materials_ingest.sql`

`001_initial.sql` has the right *shape* but no room for pipeline state. All additive
`ALTER TABLE ADD COLUMN` — no rewrites, nothing existing breaks.

```sql
-- source_files: the ingest state machine + enough to re-run extraction
ALTER TABLE source_files ADD COLUMN ingest_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (ingest_status IN ('pending','extracting','tagging','embedding','ready','failed'));
ALTER TABLE source_files ADD COLUMN ingest_error TEXT;
ALTER TABLE source_files ADD COLUMN byte_size INTEGER;
ALTER TABLE source_files ADD COLUMN stored_path TEXT;   -- NULL for pasted text
ALTER TABLE source_files ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 0;

-- chunks: position in the file, so a topic page can show chunks in reading order
ALTER TABLE chunks ADD COLUMN order_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chunks ADD COLUMN char_start INTEGER;
ALTER TABLE chunks ADD COLUMN char_end INTEGER;

-- topics: distinguish "Hermes invented this" from "the user named it"
ALTER TABLE topics ADD COLUMN created_at TEXT NOT NULL
    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'));
ALTER TABLE topics ADD COLUMN auto_created INTEGER NOT NULL DEFAULT 1
    CHECK (auto_created IN (0,1));

CREATE INDEX idx_chunks_file_order ON chunks (source_file_id, order_index);
```

`ingest_status` lives on `source_files`, not `chunks`, because the unit the user
uploaded and the unit the UI polls are both the file.

Uploaded originals go to `backend/data/uploads/<source_file_id>/<filename>` — keep them
so a failed OCR/extract can be retried without a re-upload. Add
`Settings.uploads_path = BACKEND_DIR / "data" / "uploads"`.

---

## 2. Module layout

The backend is organised **per feature**, not per layer — each feature owns its
router, repository, and schemas in one package:

```
backend/app/
  config.py  db.py  migrations.py  main.py   # shared infrastructure only
  sessions/        __init__.py router.py repository.py schemas.py
  materials/       __init__.py router.py repository.py schemas.py
                   vectors.py            # LanceDB open/upsert/search/delete
                   search.py             # search_materials(topic, query)
                   ingest/
                     __init__.py
                     extract.py          # bytes + upload_type -> plain text
                     chunker.py          # text -> [{text, char_start, char_end}]
                     tagger.py           # chunks + existing topics -> assignments
                     pipeline.py         # orchestration + status transitions
  goals/           (Step 6)
  settings/        (Step 7)
backend/agent/
  hermes.py        # Hermes gateway client
  embeddings.py    # fastembed singleton
```

The sessions slice was moved into this shape as part of this step
(`repositories/sessions.py` → `sessions/repository.py`, `routers/sessions.py` →
`sessions/router.py`, `schemas.py` → `sessions/schemas.py`); `app/repositories/` and
`app/routers/` are gone. `main.py` imports `from app.sessions import router as
sessions_router`.

Placement calls worth stating, since both could plausibly sit elsewhere:

- `vectors.py` lives **inside** `materials/`, not next to `db.py`. It is storage, but
  materials is its only consumer — per-feature ownership wins over layer purity. If a
  second feature ever needs vectors, promote it then.
- `embeddings.py` stays in `agent/` as a peer of `hermes.py`: that directory is the
  "external model clients" tier, and an embedding model is one of those regardless of
  who calls it.

### `agent/embeddings.py`

```python
@lru_cache
def _model() -> TextEmbedding: ...          # BAAI/bge-small-en-v1.5, cache_dir from settings
def embed_passages(texts: list[str]) -> list[list[float]]
def embed_query(text: str) -> list[float]   # prefixes "Represent this sentence for
                                            # searching relevant passages: "
```

The asymmetric query prefix is **not optional** for bge — omitting it measurably
degrades retrieval. Two distinct functions so a call site cannot get it wrong.

### `app/materials/vectors.py`

One LanceDB table, `chunks`, at `settings.lancedb_path`:

| column | type | why |
|---|---|---|
| `embedding_ref` | string (PK) | uuid4; mirrored into `chunks.embedding_ref` in SQLite |
| `chunk_id` | int64 | join key back to SQLite |
| `topic_id` | int64 | pre-filter for `search_materials(topic, …)` |
| `source_file_id` | int64 | so deleting a file can purge its vectors |
| `text` | string | denormalized; lets search return text without a SQLite round-trip |
| `vector` | fixed_size_list(float32, 384) | bge-small dim |

API: `upsert_chunks(rows)`, `search(vector, *, topic_id=None, limit=5)`,
`delete_by_source_file(id)`, `delete_by_chunk_ids(ids)`.

SQLite is the source of truth. LanceDB is a derived index — a repair path that rebuilds
it from `chunks` must stay possible, so never store anything in Lance that isn't also
in SQLite.

---

## 3. Pipeline stages

### 3.1 Extract (`extract.py`)

`extract(data: bytes, upload_type: str, filename: str) -> str`

- `text` — decode utf-8, `errors="replace"`.
- `pdf` — `pypdf.PdfReader`, join `page.extract_text()` with `\n\n`. If the result is
  <100 chars across the whole document, it's a scanned PDF: fail with a clear
  `ingest_error` ("no extractable text — scanned PDFs are not supported in MVP")
  rather than silently ingesting whitespace.
- `image` — `pytesseract.image_to_string(Image.open(BytesIO(data)))`. Empty result →
  `failed` with "OCR produced no text".

Every failure raises `ExtractError`, caught once in the pipeline.

**Why extraction is not delegated to Hermes.** Considered and rejected — recorded here
so it isn't reopened:

- **PDFs can't be delegated at all.** The `api_server` docs are explicit: "uploaded
  files and non-image document inputs are not supported," and non-image `data:` URLs
  return `400 unsupported_content_type`. There are no file-upload endpoints. Backend
  extraction is forced, not chosen.
- **Images could be** — `/v1/chat/completions` does accept inline `image_url` parts
  including base64 `data:image/...`. Rejected anyway: it depends on the served
  checkpoint being vision-capable (unverified — Hermes 3/4 are text-only), and a vision
  model fails *invisibly*. Tesseract produces obvious garbage; a model produces a
  plausible wrong formula that then gets embedded, tagged, and quizzed against as if it
  were the student's real material. Plain Python extraction is deterministic and
  debuggable, which matters more here than handwriting accuracy.

### 3.2 Chunk (`chunker.py`)

`chunk(text: str) -> list[Chunk]` where `Chunk = {text, char_start, char_end}`.

Paragraph-first with a size ceiling, no tokenizer dependency:

1. Split on `\n\s*\n`.
2. Accumulate paragraphs into a chunk while under **1000 chars** (≈250 tokens, well
   inside bge-small's 512-token window).
3. A single paragraph over 1000 chars gets hard-split on sentence boundaries
   (`(?<=[.!?])\s+`), falling back to a raw character split.
4. Carry **150 chars of overlap** between adjacent chunks so a definition split across
   a boundary is still retrievable.
5. Drop chunks whose stripped text is <40 chars (page numbers, headers).

Char offsets are preserved through all of it so a chunk can be located in the original.
This module is pure and deterministic — it gets unit tests, no mocks needed.

### 3.3 Tag (`tagger.py`)

MVP rule from the spec: **one chunk = exactly one topic**, LLM-classified, auto-create
when nothing fits.

`assign_topics(chunks, existing_topics) -> list[TopicAssignment]`

- Batch **20 chunks per Hermes call**. This is the cost driver: 600 chunks → 30 calls.
- Prompt sends the numbered chunk texts (truncated to ~400 chars each — the opening of
  a chunk is enough to classify it) plus `id: name — description` for every existing
  topic, and demands JSON back:
  `{"assignments":[{"index":0,"topic_id":3},{"index":1,"topic_name":"Entropy","topic_description":"…"}]}`
- `topic_id` when it matches an existing topic; `topic_name` + `topic_description` when
  it's new. Exactly one of the two, enforced on parse.
- Parse defensively — strip ``` fences, tolerate prose around the JSON. Any chunk
  missing from the response, or naming an unknown `topic_id`, is left `topic_id = NULL`.
  A NULL-topic chunk is still embedded and still searchable via untopic-filtered
  search; it just doesn't appear on a topic page. Partial tagging must never fail the
  whole upload.
- New-topic creation is case-insensitively deduped against existing names *and* against
  names created earlier in the same run — the model will propose "Entropy" twice across
  two batches. Created topics get `auto_created = 1`, `user_understanding = -1`.
- Cap new topics at **8 per upload**; beyond that, force-assign to the nearest existing
  topic by name similarity. Without this the model shreds one PDF into 40 topics and the
  Materials page is unusable.

Batches run sequentially, not concurrently — Hermes is a single local gateway and
parallel calls would just queue anyway.

### 3.4 Embed

`embed_passages()` over chunk texts in batches of 64, `upsert_chunks()` into LanceDB,
then one `UPDATE chunks SET embedding_ref = ?` per row inside a single transaction.

### 3.5 Orchestration (`pipeline.py`)

```python
async def ingest(source_file_id: int) -> None
```

Owns the status transitions and is the only place that writes `ingest_status`:

```
pending → extracting → tagging → embedding → ready
                   ↘ (any exception) ↘ failed + ingest_error
```

Each stage opens its own short `connection()` and commits — a 5-minute ingest must not
hold a write transaction open the whole time, and a crash mid-run leaves the file in a
readable non-`ready` state rather than rolling all the work back. Chunks are inserted at
the end of `extracting` (untagged, unembedded) so progress is visible immediately.

Run it with `BackgroundTasks` from the upload endpoint. Single-user system, no queue
needed — but guard against a double-submit by refusing to start if `ingest_status` is
not `pending`/`failed`.

---

## 4. HTTP surface (`materials/router.py`, prefix `/materials`)

| method | path | purpose |
|---|---|---|
| `POST` | `/materials/uploads` | multipart `file=` → 202 `{source_file_id, ingest_status}` |
| `POST` | `/materials/uploads/text` | JSON `{filename, text}` (paste box) → 202 |
| `GET` | `/materials/uploads/{id}` | poll: status, error, chunk_count, topics touched |
| `POST` | `/materials/uploads/{id}/retry` | re-run pipeline from `stored_path` after a failure |
| `DELETE` | `/materials/uploads/{id}` | cascade chunks (SQL) + purge vectors (Lance) + rm file |
| `GET` | `/materials/topics` | Materials list: name, score, chunk_count, source_count |
| `GET` | `/materials/topics/{id}` | topic-detail: topic + per-source `9 of 34 chunks` rows |
| `GET` | `/materials/topics/{id}/chunks` | paginated chunk text, `order_index` order |
| `POST` | `/materials/search` | HTTP face of `search_materials` |

`upload_type` is inferred from content-type/extension and validated against the
`source_files` CHECK constraint (`text`/`pdf`/`image`) — reject anything else with a 415
before writing a row. Cap upload size at 10MB in the endpoint (MVP target is 3–5MB).

The `GET /materials/topics/{id}` response is built directly from what the mockup shows:

```json
{
  "id": 3, "name": "Entropy", "description": "...", "user_understanding": 28,
  "chunk_count": 12,
  "sources": [
    {"source_file_id": 7, "filename": "Lecture 7 — Entropy...pdf", "upload_type": "pdf",
     "uploaded_at": "...", "chunks_in_topic": 9, "chunks_total": 34,
     "other_topics": ["Heat transfer", "Second law"]}
  ]
}
```

`chunks_in_topic` / `chunks_total` / `other_topics` is exactly the
"9 of 34 chunks tagged to Entropy · rest tagged to Heat transfer, Second law" line —
one grouped query in the repository, not three round-trips per source.

---

## 5. `search_materials(topic, query)`

The spec names this signature exactly; it is reused by Step 4 quiz generation and
Step 6 `personalize_decomposition`, so the Python function is the real artifact and
the HTTP endpoint is a thin wrapper over it.

```python
# app/materials/search.py
def search_materials(topic: str | None, query: str, *, limit: int = 5) -> list[dict]
```

It lives in `materials/` because it is materials domain logic; the Hermes tool
registry imports it from there rather than owning a second copy.

1. Resolve `topic` (a **name**, not an id — that's what a model will pass) to a
   `topic_id`: exact case-insensitive match, then `LIKE` fallback. Unresolvable topic →
   search unfiltered rather than returning nothing, and say so in the result envelope.
2. `embed_query(query)` → `vectors.search(vec, topic_id=…, limit=limit)`.
3. Return `[{chunk_id, text, topic_id, topic_name, source_filename, score}]`, hydrating
   names from SQLite in one `IN (…)` query.

Hermes tool registration is an **open item** — the `api_server` adapter is
OpenAI-compatible, so it's unconfirmed whether tools are declared per-request or
registered gateway-side in Hermes' own config. Ship the function + `POST
/materials/search` first (which satisfies Step 3's done-condition on its own), then wire
the tool declaration once the adapter's tool-calling shape is verified against a live
gateway. Don't block ingestion work on it.

---

## 6. Tests (`backend/tests/`)

Mirroring `test_sessions.py`'s throwaway-DB fixture:

- `test_chunker.py` — pure, no fixtures: size ceiling, overlap, offsets round-trip
  against the source, oversized-paragraph split, short-chunk filtering.
- `test_tagger.py` — Hermes stubbed: existing-topic match, new-topic creation,
  cross-batch dedupe of the same proposed name, malformed JSON → NULL topic not a
  crash, new-topic cap.
- `test_materials.py` — end-to-end with Hermes and fastembed both monkeypatched
  (deterministic fake vectors): upload text → poll to `ready` → topic list shows the
  topic → `search_materials` returns the planted chunk first. Also 415 on a bad type,
  404s, and delete purging both stores.

Step 3's stated done-condition — upload a file, get topics, and have
`search_materials("thermodynamics", "entropy")` return relevant chunks — is the
`test_materials.py` end-to-end case plus one manual run against real Hermes + real
fastembed with an actual lecture PDF.

---

## 7. Build order

1. Deps + `002` migration + settings/paths. (`/health` still green.)
2. `chunker.py` + its unit tests — pure, fastest feedback, no infra.
3. `extract.py` (text → pdf → image, in that order of risk).
4. `embeddings.py` + `vectors.py` + a throwaway round-trip script.
5. `materials/repository.py`.
6. `tagger.py` + tests against a stubbed Hermes.
7. `pipeline.py`.
8. `materials/router.py` + `materials/schemas.py`, registered in `main.py`.
9. `search_materials` + `POST /materials/search`.
10. End-to-end test, then the manual PDF run.

Steps 2–4 are independent of Hermes entirely and can land before the gateway is even
up.

---

## Open items

- **Hermes tool registration shape** — see §5. Needs a live gateway to verify.
- **Scanned PDFs** — rejected with a clear error; deferred, not planned. If a real test
  file turns out to be scanned, the known route is rendering pages to PNG with
  `pypdfium2` (pip-only, no poppler) and reusing the image path, capped at ~20 pages so
  one upload can't fan out into a hundred OCR passes.
- **Whiteboard/handwriting photos** — tesseract is weak on these, and the mockup's IMG
  row is exactly that case. Check the output against a real photo before the demo; if
  it's unusable, the Hermes vision path above is the fallback despite its downsides.
- **Topic merge/rename** — auto-created topics will accumulate near-duplicates over
  several uploads. No merge endpoint in this step; revisit after seeing real output.
