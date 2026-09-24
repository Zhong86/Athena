"""The MCP tool server exposed to Hermes: tool functions, the auth boundary,
and a real protocol round-trip over the mounted ASGI app.

Same stubbing pattern as test_materials.py (fake tagger, fake embedder, real
SQLite + LanceDB) -- data is seeded through the real upload pipeline rather
than hand-rolled repository calls, so a schema drift here would fail the same
way it would for a real upload.
"""

import asyncio
import json
import math
import os
import re
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp())
os.environ["SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["LANCEDB_PATH"] = str(_TMP / "lancedb")
os.environ["UPLOADS_PATH"] = str(_TMP / "uploads")
os.environ["MATERIALS_INBOX_PATH"] = str(_TMP / "materials_inbox")
os.environ["MATERIALS_GATHER_TOKEN"] = "unused-by-the-mcp-tool"
os.environ["TEST_MODE"] = "false"
os.environ["WARM_EMBEDDINGS"] = "false"
os.environ["MCP_TOKEN"] = "test-mcp-token"

from app.config import get_settings  # noqa: E402

# get_settings() is process-wide @lru_cache'd -- see test_materials_gather.py's
# identical comment. Another test module collected first could otherwise leave
# this file running against a real backend/.env's paths and secrets.
get_settings.cache_clear()

import httpx2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import mcp_tools  # noqa: E402
from app.materials import search as search_module  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402
from app.materials.gather import mcp_tools as gather_mcp_tools  # noqa: E402

VOCAB = ["entropy", "disorder", "titration"]

EXCERPT_RE = re.compile(r"\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)", re.S)
# Matches "[3] some name.pdf (text, 120 bytes)" out of relevance._prompt's listing.
CANDIDATE_RE = re.compile(r"\[(\d+)\] (.*?) \(")

ENTROPY_TEXT = (
    "Entropy is the measure of disorder in a thermodynamic system. "
    "The second law states that entropy never decreases in an isolated system. "
) * 5


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
        if "Candidate files found" in prompt:
            # gather's relevance prompt: accept anything not named "irrelevant".
            selected = [
                int(i) for i, name in CANDIDATE_RE.findall(prompt) if "irrelevant" not in name.lower()
            ]
            return json.dumps({"selected": selected})
        assignments = [
            {"index": int(index), "topic_name": "Entropy", "topic_description": "Thermo notes"}
            for index, _ in EXCERPT_RE.findall(prompt)
        ]
        return json.dumps({"assignments": assignments})

    original = (hermes.complete, embeddings.embed_passages, embeddings.embed_query)
    hermes.complete = fake_complete
    embeddings.embed_passages = lambda texts: [_fake_vector(t) for t in texts]
    embeddings.embed_query = _fake_vector
    yield
    hermes.complete, embeddings.embed_passages, embeddings.embed_query = original


@pytest.fixture(scope="module")
def client():
    init_db()
    vector_store.drop()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def seeded(client):
    """One real chunk, tagged into a real "Entropy" topic, through the actual
    ingest pipeline -- proves the MCP tools work against real schema, not a
    hand-rolled stand-in for it."""
    resp = client.post(
        "/materials/uploads/text",
        json={"filename": "entropy notes.txt", "text": ENTROPY_TEXT},
    )
    assert resp.status_code == 202, resp.text
    source_file_id = resp.json()["source_file_id"]
    uploaded = client.get(f"/materials/uploads/{source_file_id}").json()
    assert uploaded["ingest_status"] == "ready", uploaded["ingest_error"]
    return uploaded


class TestToolFunctions:
    """The plain callables in app/materials/mcp_tools.py, called directly --
    no MCP protocol involved."""

    @pytest.mark.anyio
    async def test_search_materials_finds_a_seeded_chunk(self, seeded):
        result = await mcp_tools.search_materials(query="entropy disorder")
        assert result["results"]
        assert "entropy" in result["results"][0]["text"].lower()

    @pytest.mark.anyio
    async def test_search_materials_clamps_out_of_range_limit(self, seeded, monkeypatch):
        seen = {}

        def capturing(topic, query, *, limit=5):
            seen["limit"] = limit
            return {"topic_resolved": None, "topic_matched": False, "results": []}

        monkeypatch.setattr(search_module, "search_materials", capturing)
        await mcp_tools.search_materials(query="entropy", limit=999)
        assert seen["limit"] == 50  # mirrors SearchRequest's ge=1/le=50

    @pytest.mark.anyio
    async def test_list_material_topics_returns_seeded_topics(self, seeded):
        topics = await mcp_tools.list_material_topics()
        assert any(t["name"] == "Entropy" for t in topics)


def _drop_inbox_file(name: str, text: str) -> Path:
    path = get_settings().materials_inbox_path
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / name
    file_path.write_text(text)
    return file_path


class TestGatherTool:
    """The gather_materials callable in app/materials/gather/mcp_tools.py --
    same operation as POST /materials/gather/run and the "Sync now" button,
    reachable from a chat session instead."""

    @pytest.mark.anyio
    async def test_empty_inbox_and_no_drive_returns_a_clean_zero_result(self, client):
        result = await gather_mcp_tools.gather_materials()
        assert result["candidates_seen"] == 0
        assert result["imported_file_ids"] == []
        assert "run_id" in result and "session_id" in result

        # Still logged to Knowledge-Sync even when it found nothing -- see
        # service.run_gather_cycle's docstring.
        with connection() as conn:
            row = conn.execute(
                "SELECT type FROM sessions WHERE id = ?", (result["session_id"],)
            ).fetchone()
        assert row["type"] == "cron"

    @pytest.mark.anyio
    async def test_imports_and_schedules_ingest_for_an_inbox_file(self, client):
        _drop_inbox_file(
            "gather tool check.txt",
            "Notes on entropy and disorder for the gather MCP tool test.",
        )

        result = await gather_mcp_tools.gather_materials()
        assert len(result["imported_file_ids"]) == 1
        file_id = result["imported_file_ids"][0]

        # The tool returns before ingestion finishes (extract/tag/embed run
        # as a detached task) -- wait for whatever it scheduled, same as a
        # real caller would have to poll GET /materials/uploads/{id} for.
        pending = list(gather_mcp_tools._background_ingests)
        if pending:
            await asyncio.gather(*pending)

        uploaded = client.get(f"/materials/uploads/{file_id}").json()
        assert uploaded["ingest_status"] == "ready", uploaded["ingest_error"]


class TestAuthBoundary:
    """The bearer-token middleware in app/mcp_server.py, exercised through
    the mounted /mcp path -- rejection happens before any MCP protocol
    parsing, so a plain TestClient POST is enough."""

    def test_missing_token_setting_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("MCP_TOKEN", "")
        get_settings.cache_clear()
        try:
            resp = client.post("/mcp", json={})
            assert resp.status_code == 503
        finally:
            get_settings.cache_clear()

    def test_missing_authorization_header_returns_401(self, client):
        resp = client.post("/mcp", json={})
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            "/mcp", json={}, headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 401


class TestRoundTrip:
    """A real MCP client talking to the mounted ASGI app in-process --
    httpx2.ASGITransport, no sockets, no real Hermes involved."""

    @pytest.mark.anyio
    async def test_search_materials_tool_round_trip_over_mcp(self, seeded):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer test-mcp-token"},
        ) as http_client:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http_client
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert {
                        "search_materials",
                        "list_material_topics",
                        "gather_materials",
                    } <= names

                    result = await session.call_tool(
                        "search_materials", {"query": "entropy disorder"}
                    )
                    assert not result.is_error

    @pytest.mark.anyio
    async def test_gather_materials_tool_round_trip_over_mcp(self):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer test-mcp-token"},
        ) as http_client:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http_client
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("gather_materials", {})
                    assert not result.is_error
