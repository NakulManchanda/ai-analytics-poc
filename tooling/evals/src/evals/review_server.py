"""Local-only review page: serves the interactive review.html and appends verdicts (#190)."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evals.reviews import append_reviews, new_record
from evals.run_review import DEFAULT_PHOENIX_PROJECT_ID, render_review
from evals.runs import RunError

MAX_BODY = 64 * 1024


def make_server(
    run_dir: Path,
    reviewer: str,
    baseline: Path | None = None,
    port: int = 0,
    phoenix_url: str | None = None,
    phoenix_project_id: str = DEFAULT_PHOENIX_PROJECT_ID,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _local(self) -> bool:
            """Reject DNS-rebinding / cross-site posts: Host and Origin must be this server."""
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            ok = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            return host in ok and (
                origin is None or origin in {f"http://{h}" for h in ok}
            )

        def do_GET(self) -> None:
            if self.path != "/" or not self._local():
                return self._json(404, {"error": "not found"})
            try:
                page = render_review(
                    run_dir,
                    baseline,
                    interactive=True,
                    phoenix_url=phoenix_url,
                    phoenix_project_id=phoenix_project_id,
                )
            except RunError as err:
                return self._json(500, {"error": str(err)})
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if self.path != "/api/review" or not self._local():
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= MAX_BODY:
                    raise RunError("body missing or too large")
                items = json.loads(self.rfile.read(n))["items"]
                if not isinstance(items, list) or not items:
                    raise RunError("items must be a non-empty list")
                recs = [
                    new_record(
                        run_dir,
                        i["case_id"],
                        i["turn_index"],
                        i["dimension"],
                        i["verdict"],
                        reviewer,
                        notes=str(i.get("notes", "")),
                    )
                    for i in items
                ]
                append_reviews(
                    run_dir, recs
                )  # validates the whole batch, then writes once
            except (RunError, KeyError, TypeError, ValueError) as err:
                return self._json(400, {"error": f"{type(err).__name__}: {err}"})
            self._json(200, {"saved": len(recs)})

        def log_message(self, *a) -> None:  # keep terminal quiet
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
