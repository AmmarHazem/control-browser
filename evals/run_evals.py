"""Runs the eval cases against a live service and reports the success rate."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

CASES = Path(__file__).parent / "cases.json"


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
    result_text = json.dumps(record.get("result"))
    outcome_ok = record.get("outcome") == expect.get("outcome")
    contains_ok = all(
        needle.lower() in result_text.lower() for needle in expect.get("contains", [])
    )

    return {
        "name": case["name"],
        "run_id": run_id,
        "outcome": record.get("outcome"),
        "expected": expect.get("outcome"),
        "steps": record.get("steps"),
        "seconds": round(time.monotonic() - started, 1),
        "passed": outcome_ok and contains_ok,
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
