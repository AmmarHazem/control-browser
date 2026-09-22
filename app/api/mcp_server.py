from __future__ import annotations

import base64
import json
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server

from app.browser.session import ActionRejected, ActionResult
from app.browser.toolset import dispatch, spec_by_name, tool_specs
from app.policy.network import DomainPolicy

OPEN_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "allowed_domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only these domains may be visited. Empty means no restriction.",
        },
        "blocked_domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Domains this session must not visit.",
        },
        "risky_url_patterns": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Glob patterns whose urls count as risky actions.",
        },
        "auto_snapshot": {
            "type": "boolean",
            "description": "Return a snapshot with every action. Defaults to true.",
        },
    },
    "additionalProperties": False,
}

CLOSE_SESSION_SCHEMA = {
    "type": "object",
    "properties": {"session_id": {"type": "string"}},
    "required": ["session_id"],
    "additionalProperties": False,
}


def build_mcp_server(container: Any) -> Server:
    async def on_list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name="open_session",
                description=(
                    "Open a browser session and return its session_id. Every other browser tool "
                    "takes that session_id. Close it when you are done."
                ),
                inputSchema=OPEN_SESSION_SCHEMA,
            ),
            types.Tool(
                name="close_session",
                description="Close a browser session and free its browser.",
                inputSchema=CLOSE_SESSION_SCHEMA,
            ),
        ]
        for spec in tool_specs(include_search=container.search is not None):
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.json_schema(with_session_id=True),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        args: dict[str, Any] = dict(params.arguments or {})

        if name == "open_session":
            return await _open_session(container, args)
        if name == "close_session":
            closed = await container.pool.close(str(args.get("session_id", "")))
            return _text_result(
                {"ok": closed, "error": None if closed else "no such session"}, is_error=not closed
            )

        spec = spec_by_name(name)
        if spec is None:
            return _text_result({"ok": False, "error": f"unknown tool {name}"}, is_error=True)

        session = None
        if spec.needs_session:
            session_id = str(args.pop("session_id", ""))
            session = container.pool.get(session_id)
            if session is None:
                return _text_result(
                    {
                        "ok": False,
                        "error": f"unknown session {session_id}; open one with open_session",
                    },
                    is_error=True,
                )

        try:
            result = await dispatch(name, args, session=session, search_client=container.search)
        except ActionRejected as exc:
            result = ActionResult(ok=False, error=str(exc))
        except KeyError as exc:
            result = ActionResult(ok=False, error=f"missing argument {exc}")

        if result.screenshot:
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=json.dumps(result.as_dict())),
                    types.ImageContent(
                        type="image",
                        data=base64.b64encode(result.screenshot).decode("ascii"),
                        mimeType="image/png",
                    ),
                ],
                isError=not result.ok,
            )
        return _text_result(result.as_dict(), is_error=not result.ok)

    return Server(
        "control-browser",
        version="0.1.0",
        instructions=(
            "Drive a real web browser. Open a session, navigate, then act on elements by the "
            "refs shown in each snapshot."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _open_session(container: Any, args: dict[str, Any]) -> types.CallToolResult:
    settings = container.settings
    policy = DomainPolicy(
        allowed=list(args.get("allowed_domains") or []),
        blocked=list(args.get("blocked_domains") or []),
        operator_blocked=list(settings.blocked_domains),
    )
    try:
        session = await container.pool.open(
            domain_policy=policy,
            risky_url_patterns=list(args.get("risky_url_patterns") or []),
            auto_snapshot=bool(args.get("auto_snapshot", True)),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a tool error
        return _text_result({"ok": False, "error": str(exc)}, is_error=True)
    return _text_result({"ok": True, "session_id": session.id})


def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        isError=is_error,
    )
