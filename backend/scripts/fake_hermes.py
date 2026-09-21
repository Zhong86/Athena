"""Minimal stand-in for the Hermes api_server, shaped per its docs.

Only exists to prove Athena's chat chain end-to-end while no real gateway
is available. Echoes back what it received so headers can be asserted.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

RECEIVED = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/_received":
            self._send(200, RECEIVED)
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
        })

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
