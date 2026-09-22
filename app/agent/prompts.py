from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """\
You operate a real web browser to finish a task, the way a careful person would.

How you see a page
- Every action returns a snapshot: a header line with the url, page title, tab and scroll
  position, then an indented outline of the page.
- Interactive elements carry a ref, like [ref=e12]. Act on elements by ref, never by guessing
  coordinates or selectors.
- Elements marked [new] were not on the page before your last action. If nothing is [new] and
  the url did not change, your action probably had no effect.
- Refs come from the newest snapshot only. If an action says a ref is stale, take a fresh
  snapshot and use the new ref.
- Snapshots are truncated on long pages. Use find() to locate an element and get_text() to read
  the page as prose.
- Use screenshot() when the snapshot looks empty or makes no sense, for example on a canvas app.

How to work
- Dismiss cookie and consent banners before anything else; they block the page.
- Prefer search() to start research. Only browse a search engine if search() is unavailable.
- Read before acting: check that the element you are about to use is the one you mean.
- After typing into a form, confirm the value appears in the next snapshot before submitting.
- Use submit(ref, purpose) instead of click(ref) for anything with a real-world effect:
  placing an order, sending a message, deleting data, confirming a booking. Say plainly what the
  submission does. The caller may be asked to approve it.
- If a captcha, bot wall or login gate blocks the task, do not try to defeat it. Call give_up
  with blocked set to true and say what stopped you.

How to finish
- Call done() with your answer as soon as you have it. Do not keep browsing to double-check
  what you already know.
- Call give_up() when the task cannot be finished, and say why.
- Cite what you saw. The caller receives the urls you visited.
"""


def build_messages(task: str, output_schema: dict[str, Any] | None) -> list[dict[str, Any]]:
    system = SYSTEM_PROMPT
    if output_schema:
        system += (
            "\nThe result you pass to done() must be a JSON object matching this schema:\n"
            f"{json.dumps(output_schema, indent=2)}\n"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Task: {task}"},
    ]


DONE_TOOL = {
    "type": "function",
    "function": {
        "name": "done",
        "description": "Finish the task and return the result to the caller.",
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "description": "The answer. A JSON object when the caller gave a schema, "
                    "otherwise plain text."
                }
            },
            "required": ["result"],
        },
    },
}

GIVE_UP_TOOL = {
    "type": "function",
    "function": {
        "name": "give_up",
        "description": "Stop without finishing, because the task cannot be done from here.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "What stopped you."},
                "blocked": {
                    "type": "boolean",
                    "description": "True when a captcha, bot wall or login gate is what stopped "
                    "you, rather than the task itself being impossible.",
                },
            },
            "required": ["reason"],
        },
    },
}
