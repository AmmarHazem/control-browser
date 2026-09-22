import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.integration


@asynccontextmanager
async def mcp_client(service):
    """Opened per test: the MCP streams must be entered and exited in one task."""
    async with httpx2.AsyncClient(headers={"x-api-key": "test-key"}, timeout=60) as http_client:
        async with streamable_http_client(
            f"{service.base_url}/mcp/mcp", http_client=http_client
        ) as (read_stream, write_stream, *_):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


async def test_the_toolset_is_listed_over_mcp(service):
    async with mcp_client(service) as mcp_session:
        tools = await mcp_session.list_tools()
        names = {tool.name for tool in tools.tools}

        assert {"open_session", "close_session"} <= names
        assert {"navigate", "click", "type", "submit", "snapshot", "get_text", "find"} <= names
        assert "search" not in names


async def test_every_session_scoped_tool_takes_a_session_id(service):
    async with mcp_client(service) as mcp_session:
        tools = await mcp_session.list_tools()

        for tool in tools.tools:
            if tool.name in {"open_session", "close_session"}:
                continue
            assert "session_id" in tool.input_schema["properties"], tool.name
            assert "session_id" in tool.input_schema["required"], tool.name


async def test_a_session_can_be_opened_navigated_and_closed(service, site):
    async with mcp_client(service) as mcp_session:
        session_id = await _open(mcp_session)

        navigated = await _call(mcp_session, "navigate", session_id=session_id, url=site.url("/index.html"))
        assert navigated["ok"] is True
        assert '- heading "Fixture Site"' in navigated["observation"]

        closed = await _call(mcp_session, "close_session", session_id=session_id)
        assert closed["ok"] is True


async def test_two_sessions_do_not_share_state(service, site):
    async with mcp_client(service) as mcp_session:
        first = await _open(mcp_session)
        second = await _open(mcp_session)

        await _call(mcp_session, "navigate", session_id=first, url=site.url("/form.html"))
        await _call(mcp_session, "navigate", session_id=second, url=site.url("/modal.html"))
        tabs_first = await _call(mcp_session, "list_tabs", session_id=first)

        assert "/form.html" in tabs_first["tabs"][0]["url"]


async def test_an_unknown_session_is_an_error_naming_open_session(service, site):
    async with mcp_client(service) as mcp_session:
        result = await _call(mcp_session, "navigate", session_id="nope", url=site.url("/index.html"))

        assert result["ok"] is False
        assert "open_session" in result["error"]


async def test_a_form_can_be_filled_and_submitted_over_mcp(service, site):
    async with mcp_client(service) as mcp_session:
        session_id = await _open(mcp_session)
        snapshot = await _call(
            mcp_session, "navigate", session_id=session_id, url=site.url("/form.html")
        )

        name_ref = _ref(snapshot["observation"], 'textbox "Full name"')
        typed = await _call(mcp_session, "type", session_id=session_id, ref=name_ref, text="Grace")
        assert '[value="Grace"]' in typed["observation"]

        send_ref = _ref(typed["observation"], 'button "Send message"')
        submitted = await _call(
            mcp_session, "submit", session_id=session_id, ref=send_ref, purpose="send the form"
        )

        assert submitted["ok"] is True
        assert "Submission received" in submitted["observation"]
        assert "name: Grace" in submitted["observation"]


async def test_a_screenshot_comes_back_as_image_content(service, site):
    async with mcp_client(service) as mcp_session:
        session_id = await _open(mcp_session)
        await _call(mcp_session, "navigate", session_id=session_id, url=site.url("/index.html"))

        result = await mcp_session.call_tool("screenshot", {"session_id": session_id})

        kinds = [content.type for content in result.content]
        assert kinds == ["text", "image"]
        assert result.content[1].mime_type == "image/png"


async def test_a_stale_ref_is_reported_as_a_tool_error(service, site):
    async with mcp_client(service) as mcp_session:
        session_id = await _open(mcp_session)
        snapshot = await _call(
            mcp_session, "navigate", session_id=session_id, url=site.url("/modal.html")
        )
        ref = _ref(snapshot["observation"], 'button "Open modal"')
        await _call(mcp_session, "navigate", session_id=session_id, url=site.url("/form.html"))

        result = await mcp_session.call_tool("click", {"session_id": session_id, "ref": ref})

        assert result.is_error is True
        assert "older snapshot" in json.loads(result.content[0].text)["error"]


async def _open(session: ClientSession) -> str:
    result = await session.call_tool("open_session", {})
    return json.loads(result.content[0].text)["session_id"]


async def _call(session: ClientSession, name: str, **args) -> dict:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text)


def _ref(snapshot: str, needle: str) -> str:
    for line in snapshot.splitlines():
        if needle in line and "[ref=" in line:
            start = line.index("[ref=") + len("[ref=")
            return line[start : line.index("]", start)]
    raise AssertionError(f"no ref for {needle!r} in:\n{snapshot}")
