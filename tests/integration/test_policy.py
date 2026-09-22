import asyncio
import json

import httpx
import pytest

from tests.fixture_site.service import ServiceHarness

pytestmark = pytest.mark.integration

_KEY = {"x-api-key": "test-key"}


class TestConfirmation:
    async def test_a_risky_action_pauses_a_confirm_risky_run(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/checkout.html")})
        llm.tool_on_ref("submit", 'button "Place order"', purpose="place the order")
        llm.done("order placed")

        async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
            run_id = await _start(client, {"task": "buy it", "autonomy": "confirm_risky"})
            confirmation = await _await_confirmation(client, run_id)

            assert confirmation["action"] == "submit"
            assert confirmation["purpose"] == "place the order"
            assert confirmation["source"] == "declared"
            assert "Place order" in confirmation["snapshot"]

            await client.post(f"/runs/{run_id}/confirm", json={"decision": "approve"}, headers=_KEY)
            record = await _await_finish(client, run_id)

        assert record["outcome"] == "completed"

    async def test_a_rejected_action_is_fed_back_to_the_model(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/checkout.html")})
        llm.tool_on_ref("submit", 'button "Place order"', purpose="place the order")
        llm.give_up("the caller said no")

        async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
            run_id = await _start(client, {"task": "buy it", "autonomy": "confirm_risky"})
            await _await_confirmation(client, run_id)
            await client.post(
                f"/runs/{run_id}/confirm",
                json={"decision": "reject", "note": "too expensive"},
                headers=_KEY,
            )
            record = await _await_finish(client, run_id)

        assert record["outcome"] == "failed"
        assert any("too expensive" in json.dumps(request) for request in llm.requests)

    async def test_an_unanswered_confirmation_aborts_the_run(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/checkout.html")})
        llm.tool_on_ref("submit", 'button "Place order"', purpose="place the order")

        async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
            run_id = await _start(
                client,
                {
                    "task": "buy it",
                    "autonomy": "confirm_risky",
                    "confirmation_timeout_seconds": 1,
                },
            )
            record = await _await_finish(client, run_id)

        assert record["outcome"] == "aborted"
        assert "confirmation" in record["error"]

    async def test_an_autonomous_run_never_pauses_but_still_tags_the_risk(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/checkout.html")})
        llm.tool_on_ref("submit", 'button "Place order"', purpose="place the order")
        llm.done("order placed")

        record = await _run(service, {"task": "buy it"})
        events = await _events(service, record["id"])

        assert record["outcome"] == "completed"
        risky = [event for event in events if event["type"] == "risky_action.detected"]
        assert risky and risky[0]["source"] == "declared"
        assert not [event for event in events if event["type"] == "confirmation.requested"]

    async def test_clicking_a_submit_button_is_detected_structurally(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/form.html")})
        llm.tool_on_ref("click", 'button "Send message"')
        llm.done("sent")

        record = await _run(service, {"task": "send the form with a plain click"})
        events = await _events(service, record["id"])

        risky = [event for event in events if event["type"] == "risky_action.detected"]
        assert risky and risky[0]["source"] == "structural"

    async def test_a_risky_url_pattern_is_detected_on_navigation(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/checkout.html")})
        llm.done("looked at checkout")

        record = await _run(
            service, {"task": "open checkout", "risky_url_patterns": ["*/checkout*"]}
        )
        events = await _events(service, record["id"])

        risky = [event for event in events if event["type"] == "risky_action.detected"]
        assert risky and risky[0]["source"] == "url_pattern"

    async def test_confirming_a_run_that_is_not_waiting_is_a_conflict(self, service, llm, site):
        llm.done("done")
        record = await _run(service, {"task": "nothing risky"})

        async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
            response = await client.post(
                f"/runs/{record['id']}/confirm", json={"decision": "approve"}, headers=_KEY
            )

        assert response.status_code == 409


class TestNetworkPolicy:
    async def test_a_run_allow_list_blocks_everything_outside_it(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/index.html")})
        llm.give_up("the domain was blocked")

        record = await _run(service, {"task": "go somewhere", "allowed_domains": ["example.test"]})
        events = await _events(service, record["id"])

        failures = [
            event for event in events if event["type"] == "action.result" and not event["ok"]
        ]
        assert failures
        assert "allowed-domain list" in failures[0]["error"]

    async def test_a_run_block_list_blocks_that_host(self, service, llm, site):
        llm.tool("navigate", {"url": site.url("/index.html")})
        llm.give_up("blocked")

        record = await _run(service, {"task": "go somewhere", "blocked_domains": ["127.0.0.1"]})
        events = await _events(service, record["id"])

        failures = [e for e in events if e["type"] == "action.result" and not e["ok"]]
        assert "blocked-domain list" in failures[0]["error"]

    async def test_a_non_http_scheme_is_refused(self, service, llm, site):
        llm.tool("navigate", {"url": "file:///etc/passwd"})
        llm.give_up("cannot read files")

        record = await _run(service, {"task": "read a file"})
        events = await _events(service, record["id"])

        failures = [e for e in events if e["type"] == "action.result" and not e["ok"]]
        assert "http or https" in failures[0]["error"]

    async def test_private_addresses_are_refused_when_the_guard_is_on(self, settings, llm, site):
        harness = ServiceHarness(
            settings.model_copy(
                update={
                    "llm_base_url": llm.base_url,
                    "llm_model": "fake-model",
                    "allow_private_addresses": False,
                }
            )
        ).start()
        llm.tool("navigate", {"url": site.url("/index.html")})
        llm.give_up("the address is internal")
        try:
            record = await _run(harness, {"task": "reach the internal network"})
            events = await _events(harness, record["id"])
        finally:
            harness.stop()

        failures = [e for e in events if e["type"] == "action.result" and not e["ok"]]
        assert "internal address" in failures[0]["error"]

    async def test_the_operator_block_list_cannot_be_overridden_by_a_run(self, settings, llm, site):
        harness = ServiceHarness(
            settings.model_copy(
                update={
                    "llm_base_url": llm.base_url,
                    "llm_model": "fake-model",
                    "blocked_domains": ["127.0.0.1"],
                }
            )
        ).start()
        llm.tool("navigate", {"url": site.url("/index.html")})
        llm.give_up("blocked by the operator")
        try:
            record = await _run(harness, {"task": "go anyway", "allowed_domains": ["127.0.0.1"]})
            events = await _events(harness, record["id"])
        finally:
            harness.stop()

        failures = [e for e in events if e["type"] == "action.result" and not e["ok"]]
        assert "operator blocked-domain list" in failures[0]["error"]


class TestAuth:
    async def test_a_request_without_an_api_key_is_rejected(self, service):
        async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
            response = await client.get("/sessions")

        assert response.status_code == 401

    async def test_a_wrong_api_key_is_rejected(self, service):
        async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
            response = await client.get("/sessions", headers={"x-api-key": "nope"})

        assert response.status_code == 401

    async def test_a_bearer_token_is_accepted(self, service):
        async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
            response = await client.get("/sessions", headers={"authorization": "Bearer test-key"})

        assert response.status_code == 200

    async def test_health_needs_no_key(self, service):
        async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_a_tenant_header_is_recorded_on_the_run(self, service, llm, site):
        llm.done("done")

        async with httpx.AsyncClient(base_url=service.base_url, timeout=60) as client:
            started = await client.post(
                "/runs",
                json={"task": "who am i"},
                headers={**_KEY, "x-tenant-id": "acme"},
            )
            record = await _await_finish(client, started.json()["run_id"])

        assert record["tenant_id"] == "acme"


async def _start(client: httpx.AsyncClient, payload: dict) -> str:
    response = await client.post("/runs", json=payload, headers=_KEY)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def _await_confirmation(
    client: httpx.AsyncClient, run_id: str, timeout: float = 60.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = (await client.get(f"/runs/{run_id}", headers=_KEY)).json()
        if record.get("confirmation"):
            return record["confirmation"]
        assert record["status"] != "finished", f"run finished without asking: {record}"
        await asyncio.sleep(0.1)
    raise AssertionError("no confirmation was requested in time")


async def _await_finish(client: httpx.AsyncClient, run_id: str, timeout: float = 90.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = (await client.get(f"/runs/{run_id}", headers=_KEY)).json()
        if record["status"] == "finished":
            return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


async def _run(service, payload: dict) -> dict:
    async with httpx.AsyncClient(base_url=service.base_url, timeout=120) as client:
        run_id = await _start(client, payload)
        return await _await_finish(client, run_id)


async def _events(service, run_id: str) -> list[dict]:
    async with httpx.AsyncClient(base_url=service.base_url, timeout=30) as client:
        async with client.stream("GET", f"/runs/{run_id}/events", headers=_KEY) as response:
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line.removeprefix("data: ")))
            return events
