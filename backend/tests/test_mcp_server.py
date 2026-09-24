"""The MCP tool server exposed to Hermes: tool functions, the auth boundary,
and a real protocol round-trip over the mounted ASGI app.

Same stubbing pattern as test_materials.py (fake tagger, fake embedder, real
SQLite + LanceDB) -- data is seeded through the real upload pipeline rather
than hand-rolled repository calls, so a schema drift here would fail the same
way it would for a real upload.
"""

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
os.environ["WARM_EMBEDDINGS"] = "false"
os.environ["MCP_TOKEN"] = "test-mcp-token"

import httpx2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from agent import embeddings, hermes  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.materials import mcp_tools  # noqa: E402
from app.materials import search as search_module  # noqa: E402
from app.materials import vectors as vector_store  # noqa: E402

VOCAB = ["entropy", "disorder", "titration"]

EXCERPT_RE = re.compile(r"\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)", re.S)

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
                    assert {"search_materials", "list_material_topics"} <= names

                    result = await session.call_tool(
                        "search_materials", {"query": "entropy disorder"}
                    )
                    assert not result.is_error
