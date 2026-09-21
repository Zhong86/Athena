"""Shared pytest configuration.

Async tests run on anyio's pytest plugin rather than pytest-asyncio: anyio is
already a FastAPI dependency, so this costs no new package on the VPS.
"""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Pin to asyncio -- the app runs on uvicorn/asyncio, and exercising trio
    as well would test a stack we never deploy."""
    return "asyncio"
