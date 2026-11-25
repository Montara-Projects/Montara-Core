"""Loopback-only development HTTP health surface."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from montara_core.application.health import health


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self._json(404, {"error": "not_found"})
            return
        self._json(200, health())

    def _json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Do not echo untrusted request paths to logs in this skeleton.
        pass


def create_server(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), HealthHandler)
