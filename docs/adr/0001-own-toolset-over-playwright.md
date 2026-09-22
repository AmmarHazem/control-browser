# Own thin Toolset over Playwright instead of adopting browser-use or Stagehand

The product is a Toolset — Session-scoped Actions and Observations that an *external* agent calls turn by turn — with the autonomous Run loop as a thin layer on top. browser-use and Stagehand both want to own the reasoning loop and bring their own LLM abstraction, so bending them into a Toolset means fighting their design; raw CDP would mean re-implementing waiting, frames, dialogs and downloads that Playwright already solves. We therefore write our own Action layer on Playwright-Python, and borrow browser-use's *ideas* (Refs on interactive elements, `[new]` markers) rather than its code.

## The Snapshot is collected by our own DOM walk, not by `aria_snapshot()`

This decision was originally recorded as "build the Snapshot from Playwright's `aria_snapshot()`, the same source Playwright MCP uses". Implementation showed that will not work: the public `aria_snapshot()` renders the accessibility outline **without element references**, and the ref-annotated variant Playwright MCP actually consumes is Playwright's private `_snapshot_for_ai()`. Refs are the whole point of the Snapshot — everything the model does, it does by ref — so the options were to depend on a private API that can change in any release, or to collect the tree ourselves.

We collect it ourselves: `app/browser/collect.js` walks the DOM in each same-origin frame, computes a role and an accessible name per element, stamps `data-cb-ref` on every interactive one and returns a tree that Python renders. That costs ~230 lines of JavaScript and an accessible-name heuristic that is good rather than exhaustive, and it buys refs that resolve through an ordinary attribute selector, control over what is worth showing the model (containers do not repeat their children's text), the `[new]` marker, and no private-API dependency.

Waiting, frames, dialogs, downloads and element interaction still come from Playwright, which is what the paragraph above was really protecting.

## Considered options

- **browser-use**: most popular open-source agent, fast raw-CDP perception. Rejected: loop-owning, own LLM abstraction, hard to expose as tools to a separate agent runtime.
- **Stagehand**: self-healing `act/extract/observe` over Playwright. Rejected: TypeScript-only, loop-owning in agent mode, nothing from the sibling Python services transfers.
- **Anthropic `browser_toolset_20260801`**: ready-made 31-tool browser toolset. Rejected: Claude-only; this service must work with any OpenAI-compatible model.
- **Raw CDP**: fastest. Rejected: re-implements too much of Playwright for a v1.

## Consequences

Playwright-Python is the one dependency that would take real effort to swap; keep all browser access behind the `browser/` package so a managed-browser provider (Browserbase-style, connected over CDP) stays a config switch.
