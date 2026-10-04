"""Drive a page step by step and report what the agent cannot see.

After every step it prints the elements that are visible and look interactive in the browser but
carry no ref in the snapshot. Those are the things an agent cannot act on, and they are where
"the agent could not see X" bugs live.

    .venv/bin/python scripts/inspect_page.py https://example.com \\
        --step 'type|combobox "From"|Abu Dhabi' \\
        --step 'click|option "Abu Dhabi"' \\
        --step 'wait|1.5' \\
        --show 'October'

A step is action|needle|text. The needle picks the first snapshot line containing it that carries
a ref. --show prints the snapshot lines around each match after the last step.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.browser.coverage import find_unreachable  # noqa: E402
from app.browser.pool import SessionPool  # noqa: E402
from app.browser.session import Session  # noqa: E402
from app.config import Settings  # noqa: E402


def ref_for(snapshot: str, needle: str) -> str | None:
    for line in snapshot.splitlines():
        if needle in line and "[ref=" in line:
            return line.split("[ref=")[1].split("]")[0]
    return None


async def run_step(session: Session, step: str) -> str:
    action, _, rest = step.partition("|")
    needle, _, text = rest.partition("|")
    if action == "wait":
        await asyncio.sleep(float(needle or 1))
        await session.snapshot()
        return f"waited {needle}s"
    if action == "key":
        result = await session.press_key(needle)
        return f"pressed {needle}: {result.ok}"
    ref = ref_for(session.last_snapshot, needle)
    if ref is None:
        return f"NO REF matches {needle!r}"
    if action == "click":
        result = await session.click(ref)
    elif action == "type":
        result = await session.type_text(ref, text)
    else:
        return f"unknown action {action!r}"
    return f"{action} {ref} ({needle}): {'ok' if result.ok else result.error}"


def report(unreachable: list[dict], limit: int) -> None:
    print(f"  unreachable: {len(unreachable)}")
    for item in unreachable[:limit]:
        hidden = f"  hidden by {item['hidden_by']}" if item["hidden_by"] else ""
        print(f"    <{item['tag']}> {item['why']:<16} {item['text']!r}{hidden}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("url")
    parser.add_argument("--step", action="append", default=[])
    parser.add_argument("--show", action="append", default=[])
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    settings = Settings(_env_file=None, llm_api_key="unused", browser_headless=not args.headed)
    pool = SessionPool(settings)
    await pool.start()
    try:
        session = await pool.open()
        (await session.page()).set_default_timeout(15000)
        result = await session.navigate(args.url)
        print(f"navigate: {'ok' if result.ok else result.error}")
        report(await find_unreachable(session), args.limit)
        for step in args.step:
            print(f"\n{step}\n  {await run_step(session, step)}")
            report(await find_unreachable(session), args.limit)
        lines = session.last_snapshot.splitlines()
        for needle in args.show:
            hits = [i for i, line in enumerate(lines) if needle in line]
            print(f"\n--- snapshot around {needle!r}: {len(hits)} hit(s)")
            for i in hits[:3]:
                print("\n".join(lines[max(0, i - 3) : i + 8]))
                print("  ...")
    finally:
        await pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
