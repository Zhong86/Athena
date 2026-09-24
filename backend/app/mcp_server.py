"""Assembles Athena's MCP tool server for Hermes.

One MCPServer instance, one Starlette ASGI sub-app, mounted at /mcp in
app/main.py. Domain tool functions live next to their domain (see
app/materials/mcp_tools.py); this module only wires them together, so a
second domain's tools (quizzes, cron) register here later without this
file's shape changing.
"""

import hmac

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.config import get_settings
from app.materials.gather.mcp_tools import gather_materials
from app.materials.mcp_tools import list_material_topics, search_materials


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Same posture as hermes-sidecar's require_token: refuse (503) rather
    than run open when unset; hmac.compare_digest, not ==, against a fixed
    token -- a short-circuiting comparison would leak the token prefix
    through response timing."""

    async def dispatch(self, request: Request, call_next):
        token = get_settings().mcp_token
        if not token:
            return JSONResponse(
                {"detail": "MCP_TOKEN is not set, so /mcp is disabled"}, status_code=503
            )
        expected = f"Bearer {token}"
        if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse({"detail": "bad or missing bearer token"}, status_code=401)
        return await call_next(request)


def build_app() -> Starlette:
    """A fresh MCPServer + Starlette sub-app, tools registered, auth attached.

    Not a module-level singleton: `MCPServer.streamable_http_app()`'s session
    manager can only have `.run()` entered once per instance (calling it
    twice raises). main.py's lifespan calls this fresh every time the outer
    FastAPI app's lifespan starts -- once per process in production, but once
    per test module in this repo's test suite (each opens its own
    `TestClient(app)` against the same process-wide `app`).
    """
    mcp = MCPServer(name="athena", title="Athena Materials")
    mcp.add_tool(search_materials)
    mcp.add_tool(list_material_topics)
    mcp.add_tool(gather_materials)

    # streamable_http_path="/" (not the "/mcp" default): this app is mounted
    # at /mcp in main.py, and stacking "/mcp" here on top of that mount
    # prefix would double-nest to /mcp/mcp -- verified empirically against
    # Starlette's Mount resolution before picking this.
    #
    # DNS-rebinding protection is disabled: it validates the Host header,
    # which defends a browser tricked into calling a "same-origin" internal
    # service -- not the threat model here, since Hermes' MCP client talks
    # the protocol directly and isn't a browser. Enabling it would mean
    # hardcoding an allowlist against whatever base URL ends up in Hermes'
    # own mcp_servers.athena.url, configured on a different machine and not
    # this repo's to know. The bearer token above is the real gate.
    sub_app = mcp.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    sub_app.add_middleware(BearerAuthMiddleware)
    return sub_app


class _MountProxy:
    """What main.py actually mounts at /mcp.

    Starlette's Mount binds a fixed app reference at import time, but
    build_app() must produce a fresh sub-app on every lifespan start (see
    build_app's docstring) -- this indirection lets main.py swap the live
    target on each start without re-registering the route.
    """

    def __init__(self) -> None:
        self.current: Starlette | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert self.current is not None, "mcp_server.app used before the app's lifespan started"
        await self.current(scope, receive, send)


app = _MountProxy()
