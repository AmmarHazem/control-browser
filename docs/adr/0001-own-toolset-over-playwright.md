# Own thin Toolset over Playwright instead of adopting browser-use or Stagehand

The product is a Toolset — Session-scoped Actions and Observations that an *external* agent calls turn by turn — with the autonomous Run loop as a thin layer on top. browser-use and Stagehand both want to own the reasoning loop and bring their own LLM abstraction, so bending them into a Toolset means fighting their design; raw CDP would mean re-implementing waiting, frames, dialogs and downloads that Playwright already solves. We therefore write our own ~1–2k-line Action layer on Playwright-Python, building the Snapshot from Playwright's `aria_snapshot()` (the same source Playwright MCP uses) and borrowing browser-use's *ideas* (Refs on interactive elements, `[new]` markers) rather than its code.

## Considered options

- **browser-use**: most popular open-source agent, fast raw-CDP perception. Rejected: loop-owning, own LLM abstraction, hard to expose as tools to a separate agent runtime.
- **Stagehand**: self-healing `act/extract/observe` over Playwright. Rejected: TypeScript-only, loop-owning in agent mode, nothing from the sibling Python services transfers.
- **Anthropic `browser_toolset_20260801`**: ready-made 31-tool browser toolset. Rejected: Claude-only; this service must work with any OpenAI-compatible model.
- **Raw CDP**: fastest. Rejected: re-implements too much of Playwright for a v1.

## Consequences

Playwright-Python is the one dependency that would take real effort to swap; keep all browser access behind the `browser/` package so a managed-browser provider (Browserbase-style, connected over CDP) stays a config switch.
