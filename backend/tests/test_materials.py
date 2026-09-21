"""End-to-end materials ingestion.

Hermes and the embedding model are stubbed; SQLite and LanceDB are real. The
vector store is the part most likely to break on a version bump, so faking it
would defeat the point of the test.

The fake embedder is a bag-of-words over a small vocabulary rather than random
noise, which makes retrieval assertions meaningful: a query for "entropy"
genuinely has to reach the chunk containing that word.
"""

import json
import math
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["UPLOADS_PATH"] = str(_TMP / "uploads")
# Never download the real 130MB model in a test run.
os.environ["WARM_EMBEDDINGS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import search as search_module  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402

VOCAB = ["entropy", "enthalpy", "titration", "carnot", "disorder", "quantum"]

ENTROPY_TEXT = (
    "Entropy is the measure of disorder in a thermodynamic system. "
    "The second law states that the entropy of an isolated system never "
    "decreases over time, which is why reactions run the direction they do. "
    "This idea of disorder underpins most of the thermo unit."
)
TITRATION_TEXT = (
    "Titration is a technique for determining the concentration of an unknown "
    "solution. A titration proceeds by adding a reagent of known concentration "
    "until the equivalence point is reached and the indicator changes colour."
)


def _fake_vector(text: str) -> list[float]:
    dim = get_settings().embedding_dim
    vector = [0.0] * dim
    lowered = text.lower()
    for i, word in enumerate(VOCAB):
        vector[i] = float(lowered.count(word))
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


@pytest.fixture(scope="module", autouse=True)
def stubs():
    """Module-scoped monkeypatching -- the app and its DB are module-scoped
    too, so the standard function-scoped fixture would not survive."""

    async def fake_complete(prompt, *, system=None):
        # Route each excerpt by content, the way a real tagger would, so the
        # test exercises both the existing-topic and new-topic paths.
        assignments = []
        for line in prompt.splitlines():
            if not line.startswith("["):
                continue
            index = int(line[1 : line.index("]")])
            name = "Titration" if "titration" in line.lower() else "Entropy"
            assignments.append(
                {"index": index, "topic_name": name, "topic_description": f"{name} notes"}
            )
        return json.dumps({"assignments": assignments})

    original = (hermes.complete, embeddings.embed_passages, embeddings.embed_query)
    hermes.complete = fake_complete
    embeddings.embed_passages = lambda texts: [_fake_vector(t) for t in texts]
    embeddings.embed_query = _fake_vector
    yield
    hermes.complete, embeddings.embed_passages, embeddings.embed_query = original


@pytest.fixture(scope="module")
def client(stubs):
    init_db()
    vector_store.drop()
    with TestClient(app) as c:
        yield c


def _upload_text(client, filename: str, text: str) -> dict:
    resp = client.post(
        "/materials/uploads/text", json={"filename": filename, "text": text}
    )
    assert resp.status_code == 202, resp.text
    # TestClient runs BackgroundTasks before returning, so the ingest has
    # already completed by the time we poll.
    return client.get(f"/materials/uploads/{resp.json()['source_file_id']}").json()


class TestIngest:
    def test_text_upload_runs_the_full_pipeline(self, client):
        source_file = _upload_text(client, "entropy notes.txt", ENTROPY_TEXT)

        assert source_file["ingest_status"] == "ready", source_file["ingest_error"]
        assert source_file["chunk_count"] >= 1
        assert source_file["ingest_error"] is None

    def test_chunks_are_tagged_and_embedded(self, client):
        topics = {t["name"]: t for t in client.get("/materials/topics").json()}
        assert "Entropy" in topics
        assert topics["Entropy"]["chunk_count"] >= 1
        # auto_created marks a topic Hermes invented rather than one the user named.
        assert topics["Entropy"]["auto_created"] is True
        assert topics["Entropy"]["user_understanding"] == -1

        topic_id = topics["Entropy"]["id"]
        chunks = client.get(f"/materials/topics/{topic_id}/chunks").json()
        assert chunks["total"] >= 1
        assert all(c["topic_id"] == topic_id for c in chunks["items"])

    def test_empty_text_is_rejected_by_validation(self, client):
        resp = client.post("/materials/uploads/text", json={"filename": "x", "text": ""})
        assert resp.status_code == 422

    def test_unsupported_file_type_gives_415(self, client):
        resp = client.post(
            "/materials/uploads",
            files={"file": ("lecture.mp4", b"\x00\x01binary", "video/mp4")},
        )
        assert resp.status_code == 415
        assert "unsupported file type" in resp.json()["detail"]

    def test_oversized_upload_gives_413(self, client):
        big = b"x" * (get_settings().max_upload_bytes + 1)
        resp = client.post(
            "/materials/uploads", files={"file": ("huge.txt", big, "text/plain")}
        )
        assert resp.status_code == 413

    def test_unreadable_pdf_fails_with_a_readable_error(self, client):
        pytest.importorskip("pypdf")
        resp = client.post(
            "/materials/uploads",
            files={"file": ("broken.pdf", b"%PDF-1.4 not really", "application/pdf")},
        )
        assert resp.status_code == 202
        file_id = resp.json()["source_file_id"]

        source_file = client.get(f"/materials/uploads/{file_id}").json()
        assert source_file["ingest_status"] == "failed"
        assert "PDF" in source_file["ingest_error"]

    def test_failed_upload_can_be_retried(self, client):
        pytest.importorskip("pypdf")
        resp = client.post(
            "/materials/uploads",
            files={"file": ("broken2.pdf", b"%PDF-1.4 nope", "application/pdf")},
        )
        file_id = resp.json()["source_file_id"]
        assert client.get(f"/materials/uploads/{file_id}").json()["ingest_status"] == "failed"

        retry = client.post(f"/materials/uploads/{file_id}/retry")
        assert retry.status_code == 202

    def test_retrying_a_ready_upload_gives_409(self, client):
        source_file = _upload_text(client, "retry check.txt", ENTROPY_TEXT)
        resp = client.post(f"/materials/uploads/{source_file['id']}/retry")
        assert resp.status_code == 409


class TestTopicPage:
    def test_topic_detail_reports_per_source_chunk_counts(self, client):
        """The mockup's "9 of 34 chunks tagged to Entropy · rest tagged to ..."
        line has to come from real counts."""
        mixed = f"{ENTROPY_TEXT}\n\n{TITRATION_TEXT}"
        _upload_text(client, "mixed lecture.txt", mixed)

        topics = {t["name"]: t for t in client.get("/materials/topics").json()}
        assert "Titration" in topics

        detail = client.get(f"/materials/topics/{topics['Entropy']['id']}").json()
        assert detail["name"] == "Entropy"
        assert detail["sources"]

        mixed_source = next(
            s for s in detail["sources"] if s["filename"] == "mixed lecture.txt"
        )
        assert mixed_source["chunks_in_topic"] >= 1
        assert mixed_source["chunks_total"] >= mixed_source["chunks_in_topic"]
        assert "Titration" in mixed_source["other_topics"]

    def test_missing_topic_gives_404(self, client):
        assert client.get("/materials/topics/99999").status_code == 404
        assert client.get("/materials/topics/99999/chunks").status_code == 404

    def test_missing_upload_gives_404(self, client):
        assert client.get("/materials/uploads/99999").status_code == 404


class TestSearch:
    def test_finds_the_planted_chunk(self, client):
        resp = client.post(
            "/materials/search", json={"topic": "Entropy", "query": "entropy disorder"}
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["topic_matched"] is True
        assert body["topic_resolved"] == "Entropy"
        assert body["results"]
        assert "entropy" in body["results"][0]["text"].lower()
        assert body["results"][0]["topic_name"] == "Entropy"

    def test_topic_filter_excludes_other_topics(self, client):
        body = client.post(
            "/materials/search", json={"topic": "Titration", "query": "titration"}
        ).json()
        assert body["results"]
        assert all(r["topic_name"] == "Titration" for r in body["results"])

    def test_unknown_topic_falls_back_to_searching_everything(self, client):
        """A model guessing a topic name that does not exist should still get
        material, and be told the filter was not applied."""
        body = client.post(
            "/materials/search",
            json={"topic": "Astrophysics", "query": "entropy disorder"},
        ).json()
        assert body["topic_matched"] is False
        assert body["topic_resolved"] is None
        assert body["results"]

    def test_partial_topic_name_still_resolves(self, client):
        body = client.post(
            "/materials/search", json={"topic": "entrop", "query": "disorder"}
        ).json()
        assert body["topic_matched"] is True
        assert body["topic_resolved"] == "Entropy"

    def test_direct_function_call_matches_the_spec_signature(self, client):
        """Step 3's done-condition names this call shape exactly."""
        result = search_module.search_materials("Entropy", "entropy")
        assert result["results"]
        assert result["results"][0]["chunk_id"]

    def test_blank_query_is_rejected(self, client):
        assert client.post("/materials/search", json={"query": ""}).status_code == 422


class TestDelete:
    def test_delete_purges_chunks_and_vectors(self, client):
        source_file = _upload_text(client, "disposable.txt", ENTROPY_TEXT)
        file_id = source_file["id"]
        stored = Path(get_settings().uploads_path / str(file_id))
        assert stored.exists()

        before = len(
            vector_store.search(_fake_vector("entropy disorder"), limit=50)
        )

        assert client.delete(f"/materials/uploads/{file_id}").status_code == 204
        assert client.get(f"/materials/uploads/{file_id}").status_code == 404

        after = vector_store.search(_fake_vector("entropy disorder"), limit=50)
        assert len(after) < before
        assert all(r["source_file_id"] != file_id for r in after)
        assert not stored.exists()

    def test_deleting_a_missing_upload_gives_404(self, client):
        assert client.delete("/materials/uploads/99999").status_code == 404
