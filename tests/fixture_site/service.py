from __future__ import annotations

import socket
import threading
import time

import uvicorn

from app.config import Settings
from app.main import create_app


class ServiceHarness:
    """Runs the real ASGI app on an ephemeral port, lifespan and all."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        config = uvicorn.Config(
            create_app(settings), log_level="warning", lifespan="on", access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        self._server.run(sockets=[self._socket])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 30.0) -> ServiceHarness:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("the service did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=30)
