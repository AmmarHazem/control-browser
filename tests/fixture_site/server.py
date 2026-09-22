from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PAGES = Path(__file__).parent / "pages"


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(raw)
        rows = "\n".join(
            f"    <li>{name}: {', '.join(values)}</li>" for name, values in sorted(fields.items())
        )
        body = (
            '<!doctype html>\n<html lang="en">\n'
            '<head><meta charset="utf-8"><title>Submitted</title></head>\n'
            "<body>\n  <h1>Submission received</h1>\n  <ul>\n"
            f'{rows}\n  </ul>\n  <p><a href="/index.html">Back home</a></p>\n</body>\n</html>\n'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FixtureSite:
    def __init__(self) -> None:
        handler = partial(_Handler, directory=str(PAGES))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def start(self) -> FixtureSite:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
