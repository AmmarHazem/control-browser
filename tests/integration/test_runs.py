import asyncio
import json

import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_a_run_drives_the_browser_and_completes(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")}).done("the fixture site is up")

    record = await _run(service, {"task": "check the fixture site"})

    assert record["outcome"] == "completed"
    assert record["result"] == "the fixture site is up"
    assert record["steps"] == 2


async def test_a_run_reports_the_urls_it_visited_as_sources(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("navigate", {"url": site.url("/form.html")})
    llm.done("visited two pages")

    record = await _run(service, {"task": "visit two pages"})

    assert record["sources"] == [site.url("/index.html"), site.url("/form.html")]


async def test_a_run_fills_and_submits_a_form(service, llm, site):
    llm.tool("navigate", {"url": site.url("/form.html")})
    llm.tool_on_ref("type", 'textbox "Full name"', text="Ada")
    llm.tool_on_ref("submit", 'button "Send message"', purpose="send the contact form")
    llm.done("form submitted")

    record = await _run(service, {"task": "send a message"})

    assert record["outcome"] == "completed"
    observations = [e for e in await _events(service, record["id"]) if e["type"] == "observation"]
    assert any("Submission received" in event["text"] for event in observations)


async def test_the_model_sees_the_toolset_and_the_run_tools(service, llm, site):
    llm.done("nothing to do")

    await _run(service, {"task": "do nothing"})

    names = llm.tool_names()
    assert {"navigate", "click", "type", "submit", "snapshot", "get_text", "find"} <= set(names)
    assert {"done", "give_up"} <= set(names)
    assert "search" not in names


async def test_a_result_is_validated_against_the_callers_schema(service, llm, site):
    schema = {
        "type": "object",
        "properties": {"heading": {"type": "string"}},
        "required": ["heading"],
    }
    llm.done("just a string")
    llm.done({"heading": "Fixture Site"})

    record = await _run(service, {"task": "read the heading", "output_schema": schema})

    assert record["outcome"] == "completed"
    assert record["result"] == {"heading": "Fixture Site"}


async def test_a_schema_violation_is_fed_back_to_the_model(service, llm, site):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    llm.done({"wrong": True})
    llm.done({"n": 3})

    record = await _run(service, {"task": "count", "output_schema": schema})
    events = await _events(service, record["id"])

    failures = [
        e for e in events if e["type"] == "action.result" and e["action"] == "done" and not e["ok"]
    ]
    assert failures
    assert "schema" in failures[0]["error"] or "required" in failures[0]["error"]


async def test_giving_up_fails_the_run_with_the_reason(service, llm, site):
    llm.give_up("the page does not exist")

    record = await _run(service, {"task": "find a missing page"})

    assert record["outcome"] == "failed"
    assert record["error"] == "the page does not exist"


async def test_a_captcha_wall_ends_the_run_as_blocked(service, llm, site):
    llm.give_up("a captcha blocks the results", blocked=True)

    record = await _run(service, {"task": "search something"})

    assert record["outcome"] == "blocked"


async def test_the_step_limit_fails_the_run(service, llm, site):
    for _ in range(10):
        llm.tool("snapshot", {})

    record = await _run(service, {"task": "loop forever", "max_steps": 3})

    assert record["outcome"] == "failed"
    assert "step limit" in record["error"]
    assert record["steps"] == 3


async def test_bare_text_is_nudged_before_it_is_accepted_as_an_answer(service, llm, site):
    llm.text("the answer is 42")
    llm.text("the answer is 42")

    record = await _run(service, {"task": "answer"})

    assert record["outcome"] == "completed"
    assert record["result"] == "the answer is 42"
    assert any("done()" in json.dumps(request) for request in llm.requests)


async def test_a_nudged_model_that_gives_up_fails_the_run(service, llm, site):
    llm.text("I could not finish this, the form kept changing")
    llm.give_up("the form kept rerendering")

    record = await _run(service, {"task": "fill a hostile form"})

    assert record["outcome"] == "failed"
    assert record["error"] == "the form kept rerendering"


async def test_a_run_can_be_aborted(service, llm, site):
    llm.tool("wait_for", {"seconds": 5})
    llm.done("never reached")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        started = await client.post("/runs", json={"task": "wait a while"}, headers=_KEY)
        run_id = started.json()["run_id"]
        await asyncio.sleep(0.5)
        aborted = await client.post(f"/runs/{run_id}/abort", headers=_KEY)
        assert aborted.status_code == 200
        record = await _await_finish(client, run_id)

    assert record["outcome"] == "aborted"


async def test_failed_actions_attach_a_screenshot_once_the_agent_is_stuck(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("click", {"ref": "e404"})
    llm.tool("click", {"ref": "e405"})
    llm.tool("snapshot", {})
    llm.done("recovered")

    record = await _run(service, {"task": "get stuck"})

    assert record["outcome"] == "completed"
    assert llm.sent_an_image() is True


async def test_vision_auto_downgrades_when_the_endpoint_rejects_images(service, llm, site):
    llm.reject_images = True
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("screenshot", {})
    llm.done("looked at the page")

    record = await _run(service, {"task": "look at the page"})
    events = await _events(service, record["id"])

    assert record["outcome"] == "completed"
    assert any(event["type"] == "vision.disabled" for event in events)


async def test_vision_off_never_sends_an_image(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("screenshot", {})
    llm.done("done")

    await _run(service, {"task": "look", "vision": "off"})

    assert llm.sent_an_image() is False


async def test_screenshots_are_stored_per_step_when_asked(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("screenshot", {})
    llm.done("done")

    record = await _run(service, {"task": "look", "store_screenshots": True})

    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        response = await client.get(f"/runs/{record['id']}/steps/2/screenshot", headers=_KEY)

    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_screenshots_are_not_stored_by_default(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.tool("screenshot", {})
    llm.done("done")

    record = await _run(service, {"task": "look"})

    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        response = await client.get(f"/runs/{record['id']}/steps/2/screenshot", headers=_KEY)

    assert response.status_code == 404


async def test_the_trace_is_streamed_as_server_sent_events(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")}).done("done")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        started = await client.post("/runs", json={"task": "stream me"}, headers=_KEY)
        run_id = started.json()["run_id"]
        types = []
        async with client.stream("GET", f"/runs/{run_id}/events", headers=_KEY) as response:
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    types.append(line.removeprefix("event: "))
                    if types[-1].startswith("run.") and types[-1] != "run.started":
                        break

    assert types[0] == "run.started"
    assert "step.started" in types
    assert "action.called" in types
    assert "action.result" in types
    assert "observation" in types
    assert types[-1] == "run.completed"


async def test_a_finished_runs_trace_can_be_replayed(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")}).done("done")
    record = await _run(service, {"task": "replay me"})

    events = await _events(service, record["id"])

    assert [event["type"] for event in events][:2] == ["run.started", "step.started"]
    assert events[-1]["type"] == "run.completed"


async def test_a_run_uses_a_session_the_caller_supplied_and_leaves_it_open(service, llm, site):
    llm.tool("navigate", {"url": site.url("/form.html")}).done("used the caller's session")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        opened = await client.post("/sessions", json={}, headers=_KEY)
        session_id = opened.json()["session_id"]

        started = await client.post(
            "/runs", json={"task": "use my session", "session_id": session_id}, headers=_KEY
        )
        record = await _await_finish(client, started.json()["run_id"])
        sessions = await client.get("/sessions", headers=_KEY)

    assert record["outcome"] == "completed"
    assert record["session_id"] == session_id
    assert any(row["session_id"] == session_id for row in sessions.json()["sessions"])


async def test_a_run_closes_the_session_it_opened(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")}).done("done")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        before = len((await client.get("/sessions", headers=_KEY)).json()["sessions"])
        started = await client.post("/runs", json={"task": "clean up"}, headers=_KEY)
        await _await_finish(client, started.json()["run_id"])
        after = len((await client.get("/sessions", headers=_KEY)).json()["sessions"])

    assert after == before


async def test_a_run_against_an_unknown_session_is_rejected(service, llm):
    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        response = await client.post(
            "/runs", json={"task": "nope", "session_id": "deadbeef"}, headers=_KEY
        )

    assert response.status_code == 404


async def test_run_limits_are_clamped_to_operator_caps(service, llm, site):
    llm.done("done")

    record = await _run(
        service, {"task": "clamp me", "max_steps": 10_000, "timeout_seconds": 999_999}
    )

    assert record["request"]["max_steps"] == 200
    assert record["request"]["timeout_seconds"] == 3600.0


async def test_run_limits_below_the_cap_are_honoured(service, llm, site):
    llm.done("done")

    record = await _run(service, {"task": "modest limits", "max_steps": 7})

    assert record["request"]["max_steps"] == 7


_KEY = {"x-api-key": "test-key"}


async def _run(service, payload: dict) -> dict:
    async with httpx.AsyncClient(base_url=service.base_url, timeout=120) as client:
        started = await client.post("/runs", json=payload, headers=_KEY)
        assert started.status_code == 202, started.text
        return await _await_finish(client, started.json()["run_id"])


async def _await_finish(client: httpx.AsyncClient, run_id: str, timeout: float = 90.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/runs/{run_id}", headers=_KEY)
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] == "finished":
            return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


async def _events(service, run_id: str) -> list[dict]:
    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        async with client.stream("GET", f"/runs/{run_id}/events", headers=_KEY) as response:
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
            return events
