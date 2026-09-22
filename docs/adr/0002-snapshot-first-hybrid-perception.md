# Snapshot-first hybrid perception, Screenshot on demand or when stuck

The LLM perceives a page primarily through a Snapshot — Playwright's accessibility outline as YAML with a Ref on every interactive element, a `[new]` marker on elements absent from the previous Snapshot, and a header with URL, title, tabs and scroll position — and only sees a Screenshot when it asks for one or after two consecutive failed Actions. A Snapshot costs 2–5 KB of text against 100 KB+ of image tokens for a screenshot, is more reliable for element targeting (~92% vs ~78% on common tasks in 2026 comparisons), and works with text-only models, which matters because tenant models are arbitrary OpenAI-compatible endpoints. Screenshots remain available for canvas-heavy pages and captcha walls, where structure is empty.

## Consequences

- Refs are regenerated on every Snapshot; an Action against a stale Ref fails loudly rather than guessing.
- Long pages are hard-truncated at a token budget; prose reading goes through a separate `get_text` Observation (page as Markdown) rather than through the Snapshot.
- `vision: auto` on a Run downgrades to `off` for the rest of the Run when the endpoint rejects image content.
