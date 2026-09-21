"""Minimal stand-in for the Hermes api_server, shaped per its docs.

Only exists to prove Athena's chat chain end-to-end while no real gateway
is available. Echoes back what it received so headers can be asserted.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

RECEIVED = []

# One canned run, replayed for any run_id. Enough to exercise the SSE parser,
# the tool start/complete pairing and the Knowledge-Sync trace rendering
# without a real gateway.
CANNED_EVENTS = [
    ("message.interim", {"text": "Checking what changed in your materials since Friday.", "already_streamed": False}),
    ("tool.started", {"tool": "web_search", "preview": "spaced repetition retention curve"}),
    ("tool.completed", {"tool": "web_search", "duration": 2.4, "error": False, "preview": "6 results; top hit is a 2019 meta-analysis."}),
    ("message.interim", {"text": "Two of your topics have not been reviewed in 11 days.", "already_streamed": False}),
    ("tool.started", {"tool": "read_file", "preview": "notes/linear-algebra.md"}),
    ("tool.completed", {"tool": "read_file", "duration": 0.1, "error": True, "preview": "FileNotFoundError: notes/linear-algebra.md"}),
    ("subagent.complete", {"status": "completed", "summary": "Drafted 3 quiz questions on eigenvectors.", "duration": 18.2, "child_session_id": "sess_child", "delegation_id": "dl_1"}),
    ("run.completed", {"status": "completed"}),
]

RUN_OUTPUT = "Two topics are going stale: eigenvectors and Bayes' rule. Quiz drafted for the first."


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, events):
        """Replay `events` as an SSE stream, with the keepalive comment the
        docs promise so the client's comment handling is exercised too."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b": keepalive\n\n")
        for name, data in events:
            frame = f"event: {name}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(frame.encode())
            self.wfile.flush()

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/_received":
            self._send(200, RECEIVED)
        elif self.path.startswith("/v1/runs/") and self.path.endswith("/events"):
            self._send_sse(CANNED_EVENTS)
        elif self.path.startswith("/v1/runs/"):
            self._send(200, {
                "object": "hermes.run",
                "run_id": self.path.rsplit("/", 1)[-1],
                "status": "completed",
                "output": RUN_OUTPUT,
                "usage": {"input_tokens": 50, "output_tokens": 200, "total_tokens": 250},
            })
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or "{}")

        RECEIVED.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "session_id": self.headers.get("X-Hermes-Session-Id"),
            "session_key": self.headers.get("X-Hermes-Session-Key"),
            "messages": body.get("messages"),
            "model": body.get("model"),
            "input": body.get("input"),
        })

        if self.path == "/v1/runs":
            self._send(200, {"run_id": "run_stub123", "status": "started"})
            return

        if self.path != "/v1/chat/completions":
            self._send(404, {"detail": "not found"})
            return

        turns = sum(1 for m in body.get("messages", []) if m.get("role") == "user")
        self._send(200, {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "model": "hermes-agent",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f"Stub reply (saw {turns} user message(s)).",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8642), Handler).serve_forever()
