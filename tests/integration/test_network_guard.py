import asyncio
import json

import httpx
import pytest

from app.browser.pool import SessionPool
from tests.fixture_site.service import ServiceHarness

pytestmark = pytest.mark.integration

_KEY = {"x-api-key": "test-key"}


@pytest.fixture
async def guarded_pool(settings):
    pool = SessionPool(settings.model_copy(update={"allow_private_addresses": False}))
    await pool.start()
    yield pool
    await pool.shutdown()


async def test_a_link_to_a_blocked_host_is_refused_before_the_request_leaves(guarded_pool, site):
    session = await guarded_pool.open()
    session.allow_private_addresses = True
    await session.navigate(site.url("/index.html"))
    session.allow_private_addresses = False
    session.forget_host_decisions()

    result = await session.click(_ref(session.last_snapshot, 'link "Contact form"'))

    assert "requests refused" in result.detail
    assert "internal address" in result.detail
    assert "/form.html" not in result.observation


async def test_a_blocked_domain_stops_subresources_too(pool, site):
    session = await pool.open()
    await session.navigate(site.url("/index.html"))
    session.domain_policy.blocked = ["127.0.0.1"]
    session.forget_host_decisions()

    result = await session.reload()

    assert not result.ok
    assert "refused" in result.error
    assert "blocked-domain list" in result.error


async def test_the_browser_does_not_announce_itself_as_headless(pool, site):
    session = await pool.open()
    await session.navigate(site.url("/index.html"))
    page = await session.page()

    user_agent = await page.evaluate("navigator.userAgent")

    assert "Headless" not in user_agent
    assert "Chrome" in user_agent


async def test_sources_are_recorded_even_with_auto_snapshot_off(pool, site):
    session = await pool.open(auto_snapshot=False)

    await session.navigate(site.url("/index.html"))
    await session.navigate(site.url("/form.html"))

    assert session.visited_urls == [site.url("/index.html"), site.url("/form.html")]


async def test_a_run_reports_only_the_urls_it_visited_itself(service, llm, site):
    llm.tool("navigate", {"url": site.url("/form.html")}).done("done")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        opened = await client.post("/sessions", json={}, headers=_KEY)
        session_id = opened.json()["session_id"]
        started = await client.post(
            "/runs", json={"task": "use my session", "session_id": session_id}, headers=_KEY
        )
        record = await _await_finish(client, started.json()["run_id"])

    assert record["sources"] == [site.url("/form.html")]


async def test_a_runs_domain_list_applies_to_a_caller_supplied_session(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.give_up("the domain was blocked")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        opened = await client.post("/sessions", json={}, headers=_KEY)
        session_id = opened.json()["session_id"]
        started = await client.post(
            "/runs",
            json={
                "task": "go somewhere",
                "session_id": session_id,
                "blocked_domains": ["127.0.0.1"],
            },
            headers=_KEY,
        )
        record = await _await_finish(client, started.json()["run_id"])
        events = await _events(service, record["id"])

    failures = [e for e in events if e["type"] == "action.result" and not e["ok"]]
    assert failures
    assert "blocked-domain list" in failures[0]["error"]


async def test_a_runs_domain_list_is_lifted_when_the_run_ends(service, llm, site):
    llm.tool("navigate", {"url": site.url("/index.html")})
    llm.give_up("blocked")

    async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
        opened = await client.post("/sessions", json={}, headers=_KEY)
        session_id = opened.json()["session_id"]
        started = await client.post(
            "/runs",
            json={
                "task": "go somewhere",
                "session_id": session_id,
                "blocked_domains": ["127.0.0.1"],
            },
            headers=_KEY,
        )
        await _await_finish(client, started.json()["run_id"])

        llm.tool("navigate", {"url": site.url("/index.html")}).done("reached it")
        second = await client.post(
            "/runs", json={"task": "try again", "session_id": session_id}, headers=_KEY
        )
        record = await _await_finish(client, second.json()["run_id"])

    assert record["outcome"] == "completed"
    assert record["sources"] == [site.url("/index.html")]


async def test_the_wall_clock_limit_fails_the_run(service, llm, site):
    for _ in range(20):
        llm.tool("wait_for", {"seconds": 1})

    async with httpx.AsyncClient(base_url=service.base_url, timeout=120) as client:
        started = await client.post(
            "/runs", json={"task": "dawdle", "timeout_seconds": 2}, headers=_KEY
        )
        record = await _await_finish(client, started.json()["run_id"])

    assert record["outcome"] == "failed"
    assert "time limit" in record["error"]


async def test_screenshots_are_swept_once_they_pass_their_retention(settings, llm):
    harness = ServiceHarness(
        settings.model_copy(
            update={
                "llm_base_url": llm.base_url,
                "llm_model": "fake-model",
                "screenshot_retention_seconds": 0.0,
                "screenshot_sweep_interval_seconds": 0.5,
            }
        )
    ).start()
    try:
        shot = harness.settings.screenshot_dir / "old-run" / "0001.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        async with httpx.AsyncClient(base_url=harness.base_url, timeout=30) as client:
            for _ in range(40):
                if not shot.exists():
                    break
                await client.get("/health")
                await asyncio.sleep(0.25)
    finally:
        harness.stop()

    assert not shot.exists()


async def _await_finish(client: httpx.AsyncClient, run_id: str, timeout: float = 90.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = (await client.get(f"/runs/{run_id}", headers=_KEY)).json()
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


def _ref(snapshot: str, needle: str) -> str:
    for line in snapshot.splitlines():
        if needle in line and "[ref=" in line:
            start = line.index("[ref=") + len("[ref=")
            return line[start : line.index("]", start)]
    raise AssertionError(f"no ref for {needle!r} in:\n{snapshot}")
