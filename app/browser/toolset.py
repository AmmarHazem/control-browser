from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.browser.session import ActionRejected, ActionResult, Session

Handler = Callable[["Session", dict[str, Any]], Awaitable[ActionResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, Any]
    required: list[str] = field(default_factory=list)
    handler: Handler | None = None
    needs_session: bool = True

    def json_schema(self, *, with_session_id: bool = False) -> dict[str, Any]:
        properties = dict(self.properties)
        required = list(self.required)
        if with_session_id and self.needs_session:
            properties = {
                "session_id": {
                    "type": "string",
                    "description": "The session this action runs in, from open_session.",
                },
                **properties,
            }
            required = ["session_id", *required]
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


_REF = {"type": "string", "description": "A ref from the current snapshot, such as e12."}

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "navigate",
        "Open a url in the active tab and return a snapshot of the page that loads.",
        {"url": {"type": "string", "description": "An absolute http(s) url."}},
        ["url"],
        handler=lambda s, a: s.navigate(str(a["url"])),
    ),
    ToolSpec(
        "back",
        "Go back one entry in the active tab's history.",
        {},
        handler=lambda s, a: s.go_back(),
    ),
    ToolSpec(
        "forward",
        "Go forward one entry in the active tab's history.",
        {},
        handler=lambda s, a: s.go_forward(),
    ),
    ToolSpec("reload", "Reload the active tab.", {}, handler=lambda s, a: s.reload()),
    ToolSpec(
        "click",
        "Click the element with this ref. Use it for links, buttons, checkboxes and tabs.",
        {"ref": _REF},
        ["ref"],
        handler=lambda s, a: s.click(str(a["ref"])),
    ),
    ToolSpec(
        "type",
        "Type text into the field with this ref.",
        {
            "ref": _REF,
            "text": {"type": "string", "description": "The text to enter."},
            "clear": {
                "type": "boolean",
                "description": "Replace the field's current value. Defaults to true.",
            },
            "press_enter": {
                "type": "boolean",
                "description": "Press Enter afterwards. Defaults to false.",
            },
        },
        ["ref", "text"],
        handler=lambda s, a: s.type_text(
            str(a["ref"]),
            str(a["text"]),
            clear=bool(a.get("clear", True)),
            press_enter=bool(a.get("press_enter", False)),
        ),
    ),
    ToolSpec(
        "select_option",
        "Choose an option in the dropdown with this ref, by value or visible label.",
        {"ref": _REF, "value": {"type": "string", "description": "The option value or label."}},
        ["ref", "value"],
        handler=lambda s, a: s.select_option(str(a["ref"]), str(a["value"])),
    ),
    ToolSpec(
        "hover",
        "Hover the element with this ref to reveal menus.",
        {"ref": _REF},
        ["ref"],
        handler=lambda s, a: s.hover(str(a["ref"])),
    ),
    ToolSpec(
        "press_key",
        "Press a keyboard key on the active tab, such as Escape, Enter or PageDown.",
        {"key": {"type": "string", "description": "A Playwright key name."}},
        ["key"],
        handler=lambda s, a: s.press_key(str(a["key"])),
    ),
    ToolSpec(
        "scroll",
        "Scroll the active tab, or scroll an element into view when a ref is given.",
        {
            "direction": {
                "type": "string",
                "enum": ["down", "up", "top", "bottom"],
                "description": "Which way to scroll. Defaults to down.",
            },
            "ref": {"type": "string", "description": "Optional ref to scroll into view."},
        },
        handler=lambda s, a: s.scroll(str(a.get("direction") or "down"), a.get("ref")),
    ),
    ToolSpec(
        "wait_for",
        "Wait for text to appear, for a ref to become visible, or for a number of seconds.",
        {
            "text": {"type": "string", "description": "Text to wait for."},
            "ref": {"type": "string", "description": "Ref to wait for."},
            "seconds": {"type": "number", "description": "Seconds to wait."},
        },
        handler=lambda s, a: s.wait_for(
            text=a.get("text"), ref=a.get("ref"), seconds=a.get("seconds")
        ),
    ),
    ToolSpec(
        "submit",
        "Submit a form by clicking this ref. Use this instead of click whenever the click "
        "has a real-world effect: placing an order, sending a message, deleting something. "
        "State what the submission does in purpose.",
        {
            "ref": _REF,
            "purpose": {
                "type": "string",
                "description": "What this submission will do, in one sentence.",
            },
        },
        ["ref", "purpose"],
        handler=lambda s, a: s.submit(str(a["ref"]), str(a["purpose"])),
    ),
    ToolSpec(
        "snapshot",
        "Take a fresh snapshot of the active tab.",
        {},
        handler=lambda s, a: s.snapshot(),
    ),
    ToolSpec(
        "screenshot",
        "Take a picture of the viewport. Use it when the snapshot is empty or confusing.",
        {},
        handler=lambda s, a: s.screenshot(),
    ),
    ToolSpec(
        "get_text",
        "Read the active tab's main content as Markdown. Use it for reading, not for acting.",
        {},
        handler=lambda s, a: s.get_text(),
    ),
    ToolSpec(
        "find",
        "Search the current snapshot for a substring or regular expression and return the "
        "matching lines with their refs.",
        {"query": {"type": "string", "description": "Substring or regular expression."}},
        ["query"],
        handler=lambda s, a: s.find(str(a["query"])),
    ),
    ToolSpec(
        "list_tabs", "List the tabs open in this session.", {}, handler=lambda s, a: s.list_tabs()
    ),
    ToolSpec(
        "switch_tab",
        "Make another tab active and snapshot it.",
        {"index": {"type": "integer", "description": "Tab index from list_tabs."}},
        ["index"],
        handler=lambda s, a: s.switch_tab(int(a["index"])),
    ),
    ToolSpec(
        "close_tab",
        "Close a tab. The last tab of a session cannot be closed.",
        {"index": {"type": "integer", "description": "Tab index from list_tabs."}},
        ["index"],
        handler=lambda s, a: s.close_tab(int(a["index"])),
    ),
]

SEARCH_SPEC = ToolSpec(
    "search",
    "Search the web and return result titles, urls and snippets. Start research here rather "
    "than by browsing a search engine.",
    {
        "query": {"type": "string", "description": "What to search for."},
        "max_results": {"type": "integer", "description": "How many results, default 8."},
    },
    ["query"],
    needs_session=False,
)


def tool_specs(*, include_search: bool) -> list[ToolSpec]:
    return [*TOOL_SPECS, SEARCH_SPEC] if include_search else list(TOOL_SPECS)


def spec_by_name(name: str) -> ToolSpec | None:
    for spec in tool_specs(include_search=True):
        if spec.name == name:
            return spec
    return None


async def dispatch(
    name: str,
    args: dict[str, Any],
    *,
    session: Session | None = None,
    search_client: Any = None,
) -> ActionResult:
    if name == SEARCH_SPEC.name:
        if search_client is None:
            return ActionResult(ok=False, error="search is not configured on this service")
        return await search_client.search(
            str(args.get("query", "")), max_results=int(args.get("max_results") or 8)
        )

    spec = spec_by_name(name)
    if spec is None or spec.handler is None:
        return ActionResult(ok=False, error=f"unknown tool {name}")
    if session is None:
        return ActionResult(ok=False, error=f"{name} needs a session")
    return await spec.handler(session, args)


async def call_tool(
    name: str,
    args: dict[str, Any],
    *,
    session: Session | None = None,
    search_client: Any = None,
) -> ActionResult:
    """Dispatch, turning the two argument faults a caller can cause into readable results."""
    try:
        return await dispatch(name, args, session=session, search_client=search_client)
    except ActionRejected as exc:
        return ActionResult(ok=False, error=str(exc))
    except KeyError as exc:
        return ActionResult(ok=False, error=f"missing argument {exc}")
