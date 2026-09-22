# Browser Agent v1

Triage: `ready-for-agent`

## Problem Statement

Developers and services that use LLM agents can search the web and read pages, but they cannot *act* on the web. The existing tooling (metasearch plus one-shot "fetch this URL and answer a question") is stateless and read-only: no clicking, no typing, no form submission, no memory of what happened one step ago. Any Task that a person would do in a browser — compare prices across several sites, fill and submit a form, follow a multi-page flow, dig through a site's navigation to find a fact — is out of reach. Teams that want this today must either hand-code brittle scripts per website or adopt a full-blown autonomous browser agent that owns the reasoning loop and cannot be mixed with the agent they already have.

## Solution

A standalone service that lets any OpenAI-compatible LLM operate a server-side headless browser the way a person does. It exposes the browser as a **Toolset**: stateful **Sessions** in which an agent performs **Actions** (navigate, click, type, select, scroll, submit…) and receives **Observations** — a compact text **Snapshot** of the page with a **Ref** on every interactive element, and a **Screenshot** when structure is not enough. Any external agent can drive the Toolset turn by turn over MCP. For callers without an agent, a **Run** endpoint accepts a natural-language **Task** and drives the LLM loop itself, streaming a live **Trace** and returning a result (optionally shaped by a caller-supplied JSON Schema) plus the **Sources** visited. A caller-chosen **Autonomy Level** decides whether **Risky Actions** proceed or pause for **Confirmation**. Network policy, rate limits and resource caps keep it safe to run inside a private cluster.

## User Stories

### Sessions and navigation

1. As a developer, I want to open a Session and receive its id, so that I can drive one isolated browser across many calls.
2. As a developer, I want each Session to have its own cookies and storage, so that two concurrent Sessions never leak state into each other.
3. As a developer, I want to navigate a Session to a URL and immediately receive an Observation of the loaded page, so that I do not need a second call to see where I landed.
4. As a developer, I want `back`, `forward` and `reload` Actions, so that the agent can move through history like a person.
5. As a developer, I want to list, switch between and close tabs inside a Session, so that links that open new tabs and popups do not lose the agent.
6. As a developer, I want a Session that I stop using to be closed automatically after an idle period, so that abandoned browsers do not exhaust memory.
7. As a developer, I want a hard maximum Session lifetime, so that no Session can live forever regardless of activity.
8. As a developer, I want to close a Session explicitly, so that I can free resources as soon as I am done.
9. As an operator, I want the number of concurrent Sessions per instance to be capped and to shrink further under memory pressure, so that one instance never falls over from too many browsers.

### Perceiving the page

10. As an agent, I want a Snapshot that lists the page's structure and interactive elements as compact text, so that I can understand the page cheaply without images.
11. As an agent, I want every interactive element in a Snapshot to carry a short Ref, so that I can target it precisely without coordinates or CSS selectors.
12. As an agent, I want elements that were not present in my previous Snapshot to be marked `[new]`, so that I can tell whether my last Action changed anything.
13. As an agent, I want each Snapshot to start with the current URL, page title, number of tabs and scroll position, so that I always know where I am.
14. As an agent, I want a very long page's Snapshot to be truncated at a token budget with an explicit "truncated" trailer, so that my context is never flooded and I know to scroll or read differently.
15. As an agent, I want a `get_text` Observation that returns the page as clean Markdown, so that I can read an article or thread as prose rather than as an outline.
16. As an agent, I want a `find` Observation that searches the current Snapshot for text and returns matching Refs, so that I can locate one element on a huge page without reading all of it.
17. As an agent, I want a `screenshot` Observation on demand, so that I can look at canvas-only or image-heavy pages when structure tells me nothing.
18. As a developer, I want a Screenshot to be attached to the next Observation automatically after two consecutive failed Actions, so that a stuck agent gets a new perspective without me intervening.
19. As a developer, I want to set `vision` to `auto`, `on` or `off` per Run, so that screenshots are only sent to models that can read them.
20. As a developer, I want a Run with `vision: auto` to detect that the model rejects images and stop sending them for the rest of that Run, so that a text-only model does not fail the whole Task.
21. As a developer, I want the Snapshot and `get_text` token budgets to be configurable per Run within operator caps, so that I can trade cost for completeness.

### Acting on the page

22. As an agent, I want to click an element by Ref, so that I can press buttons and follow links.
23. As an agent, I want to type into an element by Ref, optionally clearing it first and optionally pressing Enter afterwards, so that I can fill fields and search boxes in one Action.
24. As an agent, I want to select an option in a dropdown by Ref and value, so that I can complete select fields.
25. As an agent, I want to hover an element by Ref, so that I can reveal hover menus.
26. As an agent, I want to press a keyboard key, so that I can dismiss overlays or trigger shortcuts.
27. As an agent, I want to scroll in a direction or to a Ref, so that I can bring off-screen content into view.
28. As an agent, I want to wait for text to appear, for a Ref to exist, or for a number of seconds, so that I can handle pages that load asynchronously.
29. As an agent, I want a dedicated `submit` Action that takes a Ref and a stated purpose, so that I can declare when I am about to do something with real-world effect.
30. As an agent, I want an Action on a Ref that no longer exists to fail with a message telling me to take a new Snapshot, so that I never act on the wrong element.
31. As an agent, I want each Action to return the Observation of the page after it, so that one round-trip covers acting and seeing.
32. As a developer, I want to turn automatic Observations off per Session, so that I can chain several Actions cheaply and take one Snapshot at the end.
33. As an agent, I want native browser dialogs (alert, confirm, prompt) to be handled automatically and reported in the Action result, so that a Session never hangs on a modal.
34. As an agent, I want elements inside same-origin iframes to appear in the Snapshot, so that embedded forms are reachable.
35. As a developer, I want every Action to time out after a bounded period, so that a hung page cannot stall a Run.

### Searching

36. As an agent, I want a `search` tool that returns web results for a query, so that I can start a research Task without navigating a search engine page.
37. As an operator, I want the `search` tool to disappear cleanly when no search backend is configured, so that the service still starts and the agent knows to browse a search engine instead.

### Runs

38. As a developer, I want to submit a Task and immediately receive a Run id, so that long Tasks do not block my request.
39. As a developer, I want to stream a Run's Trace as events while it executes, so that I can show progress and diagnose failures live.
40. As a developer, I want the Trace to include each Step's reasoning text, Action call, Action result and Observation, so that I can reconstruct exactly what the agent did and saw.
41. As a developer, I want to fetch a finished Run's record and Trace later, so that I can audit or debug after the fact.
42. As a developer, I want a Run to end with a clear Outcome — Completed, Failed, Aborted or Blocked — so that my code can branch on what happened.
43. As a developer, I want a Run that meets a captcha or login wall to end as Blocked rather than attempt to beat it, so that I get an honest answer quickly.
44. As a developer, I want to abort a running Run, so that I can stop wasted work.
45. As a developer, I want to pass a JSON Schema for the result, so that the Run returns an object my code can consume without parsing prose.
46. As a developer, I want a Run without a schema to return a free-text result, so that simple Tasks stay simple.
47. As a developer, I want every Run to return the list of URLs it visited as Sources, so that I can cite or verify where an answer came from.
48. As a developer, I want a Run to give up with a stated reason when the Task is impossible, so that I learn why rather than hitting a step limit.
49. As a developer, I want limits on Steps and wall-clock time per Run with sensible defaults and per-Run overrides within operator caps, so that runaway Tasks are bounded.
50. As a developer, I want to hand a Run an existing Session id, so that I can pre-navigate or inspect the browser before and after the Run.
51. As a developer, I want a Run given no Session to open one and close it when finished, so that the simple case needs no cleanup.
52. As a developer, I want a Run given an existing Session to leave it open when finished, so that I can continue working in it.
53. As a developer, I want to choose the model, base URL and API key per Run, so that different callers can use different LLM endpoints on the same service.
54. As an operator, I want service-level LLM defaults from the environment, so that callers who do not specify a model still work.

### Autonomy and safety

55. As a developer, I want to choose an Autonomy Level per Run, so that I decide how much freedom the agent has.
56. As a developer, I want `autonomous` to be the default level, so that a Run never pauses unless I ask for it.
57. As a developer, I want a `confirm_risky` Run to pause before each Risky Action and emit a Confirmation request with the Step, Action, stated purpose and current Snapshot, so that a human or my code can decide.
58. As a developer, I want to approve or reject a pending Confirmation, optionally with a note, so that I can steer the Run.
59. As an agent, I want a rejected Confirmation to reach me as the Action's result including the caller's note, so that I can choose another path.
60. As a developer, I want a Confirmation that nobody answers to time out and end the Run as Aborted, so that a forgotten Run does not hold a browser indefinitely.
61. As a developer, I want the Session's idle timer to be suspended while a Confirmation is pending, so that waiting for a human does not kill the Session.
62. As a developer, I want Risky Actions detected three ways — declared by the agent via `submit`, detected structurally as a form submission, or matched against URL patterns I supply — so that neither a sloppy model nor an over-eager rule decides alone.
63. As an auditor, I want every detected Risky Action tagged in the Trace even in `autonomous` Runs, so that I can review what the agent did with real-world effect.
64. As an operator, I want requests to private and internal network addresses blocked, so that the agent cannot be turned into a probe inside my cluster.
65. As a developer, I want to pass allowed and blocked domain lists per Run, so that I can fence a Task to the sites it should touch.
66. As an operator, I want a global blocked-domain list in configuration that no Run can override, so that organisation-wide rules hold.
67. As an operator, I want navigations to the same host to be rate-limited, so that the service is a polite visitor and does not get itself blocked.
68. As an operator, I want the browser to present itself as an ordinary Chrome (realistic user agent, viewport, locale, timezone) without fingerprint evasion, so that the service behaves like a real browser and not like an evasion toolkit.
69. As a developer, I want the agent to be unable to execute arbitrary JavaScript on pages, so that text on a hostile page cannot escalate into code execution.

### Integration

70. As an agent-runtime developer, I want the Toolset exposed as an MCP server over HTTP, so that any MCP-capable agent can drive the browser without a custom adapter.
71. As a developer, I want every MCP tool to take an explicit `session_id`, so that one connection can drive several Sessions and the REST and MCP views of a Session are the same thing.
72. As a developer, I want the REST API described by an OpenAPI document, so that I can generate clients.
73. As an operator, I want the API protected by an API key header, so that only permitted callers reach it when deployed standalone.
74. As a platform developer, I want a tenant header to be accepted and recorded when present, so that plugging the service into a multi-tenant platform later needs no API change.
75. As an operator, I want structured JSON logs with Session and Run ids on every line, so that I can follow one Run through the logs.
76. As a developer, I want a minimal debug web page served by the service where I can submit a Task and watch its Trace and Screenshots live, so that I can tune prompts by watching rather than reading logs.
77. As an operator, I want a container image with the browser baked in and a compose file with an optional search backend, so that I can run the whole thing with one command.
78. As a developer, I want optional per-Step Screenshots stored with a retention period and served per Step, so that I can replay a Run visually when debugging.

## Implementation Decisions

Vocabulary follows `CONTEXT.md`; the decisions below respect ADR 0001 (own Toolset over Playwright), ADR 0002 (Snapshot-first hybrid perception) and ADR 0003 (browser as Toolset, Run on top).

### Shape of the service

- Python 3.13, FastAPI, Playwright-Python, the official MCP Python SDK mounted into the same ASGI app, OpenAI Agents SDK with its chat-completions model adapter for the Run loop. Tooling: uv, ruff, mypy, pytest, conventional commits.
- Package layout by capability: an API package (REST routes, SSE, MCP mount, static debug UI), a browser package (Session, pool, Snapshot builder, Refs, Actions), an agent package (Run loop, prompts, output-schema handling), a policy package (Autonomy Levels, Risky Action detection, domain lists, SSRF guard, per-host rate limiter) and a storage package (Run/Trace repository, Screenshot store). Single replica; no sticky routing.
- All browser access goes through the browser package so a remote CDP endpoint can replace the local Chromium by configuration later.

### Session model

- A Session wraps one isolated browser context and its tabs. Ids are opaque strings issued by `open_session`. Sessions are held in-process and die with the process.
- Pool with a hard concurrent-Session cap (default 25) and graduated memory-pressure levels (create freely / delay / reuse only / reclaim), a janitor enforcing idle TTL (default 5 min) and max lifetime (default 60 min). The idle timer is suspended while a Confirmation is pending.
- Per-Session `auto_snapshot` flag (default on): when on, every mutating Action's result carries the post-Action Observation.
- A storage-state hook (save/restore of a Session's cookies and local storage) exists as an interface with no implementation, reserving the seam for per-user profiles.
- Browser presents as ordinary Chrome: new headless mode, realistic user agent, fixed desktop viewport, configurable locale and timezone. No stealth libraries.
- Native dialogs are auto-accepted (or dismissed for prompts) and reported in the Action result. Popups become new tabs in the Session.

### Observation model

- Snapshot = header line (URL, title, tab index/count, scroll position) + Playwright's aria snapshot rendered as YAML, with `[ref=eN]` on every interactive element and `[new]` on elements absent from the Session's previous Snapshot. Refs are regenerated per Snapshot and resolved through Playwright's aria-ref locators; a stale Ref yields a structured error instructing a fresh Snapshot. Hard-truncated at a token budget (default 8k) with an explicit trailer.
- `get_text` = main-content Markdown of the current page (readability-style extraction then HTML→Markdown), budget default 16k.
- `find(query)` = substring/regex search over the current Snapshot returning matching lines with Refs.
- `screenshot` = viewport PNG, returned as image content. Stuck detector: after two consecutive failed Actions in a Run, the next Observation includes a Screenshot. `vision` per Run: `auto` (default; on image rejection by the endpoint, downgrade to `off` for the Run and log), `on`, `off`.

### Toolset (MCP)

- Tools, all with `session_id` except `open_session`: `open_session`, `close_session`, `list_tabs`, `switch_tab`, `close_tab`, `navigate`, `back`, `forward`, `reload`, `click`, `type` (`clear` default true, `press_enter` default false), `select_option`, `hover`, `press_key`, `scroll`, `wait_for`, `snapshot`, `screenshot`, `get_text`, `find`, `search` (registered only when a SearXNG URL is configured), `submit(ref, purpose)`.
- Excluded on purpose: arbitrary JavaScript execution, LLM-powered structured extraction, drag, upload, download.
- MCP transport is Streamable HTTP under the same host as REST. Tool results carry a structured `ok`/`error` envelope plus the Observation when applicable.

### Runs (REST)

- `POST /runs` accepts: `task`, optional `output_schema` (JSON Schema), optional `session_id`, `autonomy` (`autonomous` default | `confirm_risky`), `vision`, `allowed_domains`, `blocked_domains`, `risky_url_patterns`, `store_screenshots` (default false), limit overrides (`max_steps` default 50, `timeout` default 10 min, `confirmation_timeout` default 10 min, snapshot and text budgets), and optional LLM overrides (`model`, `base_url`, `api_key`). Returns the Run id immediately.
- `GET /runs/{id}` returns the record (status, Outcome, result, Sources, timings, limits used). `GET /runs/{id}/events` streams the Trace as SSE from the beginning and then live. `POST /runs/{id}/confirm` with `decision` (`approve`|`reject`) and optional `note`. `POST /runs/{id}/abort`. `GET /runs/{id}/steps/{n}/screenshot` when stored.
- Trace event types: `run.started`, `step.started`, `step.reasoning`, `action.called`, `action.result`, `observation`, `risky_action.detected`, `confirmation.requested`, `confirmation.resolved`, `run.completed`, `run.failed`, `run.aborted`, `run.blocked`. Observations in events are truncated copies.
- Run loop: OpenAI Agents SDK agent whose tools are thin wrappers over the same Toolset bound to the Run's Session (no private shortcuts), plus Run-only tools `done(result)` and `give_up(reason)`. `done` validates against `output_schema` when given and feeds validation errors back to the model. Sources are collected from every navigation and final URL.
- Outcome mapping: `done` → Completed; `give_up`, max steps, timeout, unrecoverable error → Failed with reason; caller abort or Confirmation timeout → Aborted; agent reports a captcha/login wall via `give_up` with a blocked category → Blocked.
- Session ownership: no `session_id` → Run opens and closes its own; given `session_id` → Run uses it and leaves it open.

### Policy

- Autonomy Levels: `autonomous` (never pause) and `confirm_risky` (pause before each Risky Action). Both levels tag `risky_action.detected` in the Trace.
- Risky Action detection is the union of: agent-declared `submit`; structural detection (click on a submit-type control or an Enter press inside a form); caller-supplied URL patterns matched against the current or target URL. Under `confirm_risky`, detection emits `confirmation.requested` and blocks the Action until `approve`, `reject` (note returned to the model as the Action result) or timeout (Run → Aborted).
- Network policy evaluated on every navigation and on redirects: SSRF guard (private, loopback, link-local, metadata ranges; DNS resolved before connect), then operator global deny-list, then per-Run deny-list, then per-Run allow-list if present. Violations fail the Action with a policy error the agent can read.
- Per-host rate limiter on navigations (default 1 s minimum spacing per host).

### Storage and observability

- Run records and Trace events persisted through a repository interface; v1 implementation is SQLite in a single file. Screenshots, when enabled, are files under a per-Run folder with a retention sweeper (default 24 h).
- Structured JSON logs via the standard logging stack with Session id, Run id and Step number bound as context. No metrics or tracing in v1.

### Configuration and auth

- Environment-driven settings: port, API keys, LLM defaults, SearXNG URL (optional), global blocked domains, pool caps, TTLs, budgets, storage paths, log level. Per-Run overrides are clamped to operator caps.
- API key in a request header for REST and MCP; `X-TENANT-ID` parsed and attached to Run records when present, otherwise ignored.

### Deliverables

- Dockerfile (Chromium baked in), `docker-compose.yml` with the service and an optional SearXNG, `.env.example`, README, `docs/adr`, `docs/spec`, the single-page debug UI, and an `evals/` folder with ~10 real-web Tasks and a runner that reports success rate.

## Testing Decisions

- A good test drives the service the way a caller would and asserts only on what a caller can see: Snapshot text, Action result envelopes, Trace events, Run Outcomes and results, HTTP status codes. Tests do not reach into pool internals, Ref bookkeeping or repository rows.
- One seam: the in-process ASGI app (REST + SSE + MCP mount) exercised through an HTTP test client and an in-process MCP client, against a real Chromium and an in-process **fixture site** — a handful of static HTML pages covering a multi-field form with a submit button, a link that opens a new tab, a modal and an alert, a cookie banner, a same-origin iframe with a form, a very long article, a page that mutates after a delay, and a fake "checkout" URL for Risky Action pattern tests.
- The LLM is replaced by a **fake OpenAI-compatible endpoint** running in-process that replays scripted responses (tool calls and final `done`/`give_up`), so Run tests are deterministic and cover Confirmation flows, `vision: auto` downgrade (the fake rejects image content), output-schema validation feedback, step and time limits, and every Outcome.
- Unit tests, kept to pure functions with many cases: Snapshot rendering and truncation from a fixed aria tree, `[new]` marking, structural Risky Action detection, domain-list and URL-pattern matching, SSRF address classification, per-Run limit clamping.
- Evals live outside the test suite: ~10 real-web Tasks with expected outcomes, run manually or nightly against a real model to measure Run success rate as prompts change; never part of CI pass/fail.
- Prior art: the sibling agentfabric web-search-service uses pytest with unit/integration/e2e markers, `respx` for HTTP mocking and a `MagicMock` over the Agents SDK in `conftest`; this repo follows the marker split but replaces SDK mocking with the fake LLM endpoint so the real Agents SDK is exercised.

## Out of Scope

- Acting on sites that require the user's own login; persisted per-user browser profiles (only the storage-state interface is reserved).
- An `observe` read-only Autonomy Level.
- File downloads, file uploads, PDF rendering, drag-and-drop, arbitrary JavaScript execution, LLM-powered structured extraction.
- Cross-origin iframe content beyond what Playwright's aria snapshot exposes.
- Captcha solving or any detection-evasion.
- Multi-replica deployment, sticky Session routing, Postgres, Redis-backed per-tenant LLM configuration.
- Prometheus metrics and OpenTelemetry traces.
- Bundling SearXNG inside the service image (it is an optional external dependency).
- Integration into the agentfabric monorepo, Helm charts and tenant middleware.

## Further Notes

- The Toolset is the durable asset; the Run loop is a convenience. If the loop ever moves into an external agent runtime, nothing in the Toolset should need to change (ADR 0003).
- Suggested build order: browser package plus fixture site and Snapshot tests → MCP Toolset → policy package → Runs, Trace and storage → debug UI → Docker/compose → evals.
- Code comments are not used in source files; rationale lives in commit messages, ADRs and docs.
