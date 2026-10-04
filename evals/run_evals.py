"""Runs the eval cases against a live service and reports the success rate."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

CASES = Path(__file__).parent / "cases.json"
DATE_TOKEN = re.compile(r"\{date([+-]\d+)?:([^}]+)\}")


def expand(text: str) -> str:
    """Expand {date+3:%Y-%m-%d} into a date relative to today, so expectations can be exact."""

    def render(match: re.Match[str]) -> str:
        offset = int(match.group(1) or 0)
        return (date.today() + timedelta(days=offset)).strftime(match.group(2))

    return DATE_TOKEN.sub(render, text)


async def run_case(client: httpx.AsyncClient, case: dict[str, Any], timeout: float) -> dict:
    payload = {"task": case["task"], "timeout_seconds": timeout}
    if case.get("output_schema"):
        payload["output_schema"] = case["output_schema"]

    started = time.monotonic()
    response = await client.post("/runs", json=payload)
    response.raise_for_status()
    run_id = response.json()["run_id"]

    deadline = time.monotonic() + timeout + 60
    record: dict[str, Any] = {}
    while time.monotonic() < deadline:
        record = (await client.get(f"/runs/{run_id}")).json()
        if record["status"] == "finished":
            break
        await asyncio.sleep(2)

    expect = case.get("expect", {})
    result_text = json.dumps(record.get("result")).lower()
    accepted = expect.get("outcome")
    accepted = [accepted] if isinstance(accepted, str) else list(accepted or [])
    outcome_ok = record.get("outcome") in accepted
    contains_ok = all(
        expand(needle).lower() in result_text for needle in expect.get("contains", [])
    )
    excludes_ok = all(
        expand(needle).lower() not in result_text for needle in expect.get("not_contains", [])
    )
    sources = [url.lower() for url in record.get("sources") or []]
    sources_ok = all(
        any(expand(needle).lower() in url for url in sources)
        for needle in expect.get("sources_contain", [])
    )

    return {
        "name": case["name"],
        "run_id": run_id,
        "outcome": record.get("outcome"),
        "expected": "|".join(accepted),
        "steps": record.get("steps"),
        "seconds": round(time.monotonic() - started, 1),
        "passed": outcome_ok and contains_ok and excludes_ok and sources_ok,
        "result": record.get("result"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8100")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--only", default="", help="run one case by name")
    args = parser.parse_args()

    cases = json.loads(CASES.read_text())
    if args.only:
        cases = [case for case in cases if case["name"] == args.only]

    headers = {"x-api-key": args.api_key} if args.api_key else {}
    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=None) as client:
        results = [await run_case(client, case, args.timeout) for case in cases]

    passed = sum(1 for result in results if result["passed"])
    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        print(
            f"{mark}  {result['name']:<24} outcome={result['outcome']} "
            f"(expected {result['expected']}) steps={result['steps']} "
            f"{result['seconds']}s run={result['run_id']}"
        )
    print(f"\n{passed}/{len(results)} passed")


if __name__ == "__main__":
    asyncio.run(main())
