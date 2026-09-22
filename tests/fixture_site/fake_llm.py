from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeLLM:
    """An OpenAI-compatible chat endpoint that replays scripted turns."""

    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.reject_images = False
        self._lock = threading.Lock()
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # ---- scripting -------------------------------------------------------

    def tool(self, name: str, args: dict[str, Any] | None = None, *, say: str = "") -> FakeLLM:
        return self.tools([(name, args or {})], say=say)

    def tools(self, calls: list[tuple[str, dict[str, Any]]], *, say: str = "") -> FakeLLM:
        self.turns.append(
            {
                "content": say or None,
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                    for index, (name, args) in enumerate(calls)
                ],
            }
        )
        return self

    def tool_on_ref(self, name: str, needle: str, **extra: Any) -> FakeLLM:
        """Queue a turn whose ref is read from the newest snapshot at request time."""

        def resolve(request: dict[str, Any]) -> dict[str, Any]:
            ref = _find_ref(request, needle)
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_0",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"ref": ref, **extra}),
                        },
                    }
                ],
            }

        self.turns.append(resolve)
        return self

    def text(self, content: str) -> FakeLLM:
        self.turns.append({"content": content, "tool_calls": None})
        return self

    def done(self, result: Any) -> FakeLLM:
        return self.tool("done", {"result": result})

    def give_up(self, reason: str, *, blocked: bool = False) -> FakeLLM:
        return self.tool("give_up", {"reason": reason, "blocked": blocked})

    # ---- inspection ------------------------------------------------------

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def last_request(self) -> dict[str, Any]:
        return self.requests[-1]

    def sent_an_image(self) -> bool:
        return any(_has_image(request) for request in self.requests)

    def tool_names(self) -> list[str]:
        if not self.requests:
            return []
        return [tool["function"]["name"] for tool in self.requests[-1].get("tools", [])]

    def next_turn(self, request: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            turn = self.turns.pop(0) if self.turns else None
        if callable(turn):
            return turn(request)
        return turn

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> FakeLLM:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _find_ref(request: dict[str, Any], needle: str) -> str:
    for message in reversed(request.get("messages", [])):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        observation = content
        if content.lstrip().startswith("{"):
            try:
                observation = json.loads(content).get("observation") or ""
            except json.JSONDecodeError:
                observation = content
        for line in observation.splitlines():
            if needle in line and "[ref=" in line:
                start = line.index("[ref=") + len("[ref=")
                return line[start : line.index("]", start)]
    raise AssertionError(f"no snapshot line with a ref matches {needle!r}")


def _has_image(request: dict[str, Any]) -> bool:
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            if any(part.get("type") == "image_url" for part in content):
                return True
    return False


def _make_handler(fake: FakeLLM):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self._json(404, {"error": {"message": f"no route for {self.path}"}})
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            fake.requests.append(body)

            if fake.reject_images and _has_image(body):
                self._json(
                    400,
                    {
                        "error": {
                            "message": "This model does not support image input",
                            "type": "invalid_request_error",
                        }
                    },
                )
                return

            turn = fake.next_turn(body)
            if turn is None:
                self._json(500, {"error": {"message": "the fake llm script ran out of turns"}})
                return

            self._json(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model", "fake"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": turn["content"],
                                "tool_calls": turn["tool_calls"],
                            },
                            "finish_reason": "tool_calls" if turn["tool_calls"] else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler
