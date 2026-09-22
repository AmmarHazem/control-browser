# control-browser

Lets an LLM operate a real web browser the way a person does: open pages, read them, click, type,
submit forms, and carry state from one step to the next.

Two ways in, one core:

- **Toolset (MCP)** — stateful browser Sessions exposed as tools your own agent calls turn by turn.
- **Runs (REST)** — hand it a Task in plain language and it drives the LLM loop itself, streaming a
  live Trace and returning a result plus the Sources it visited.

Works with any OpenAI-compatible model. The vocabulary used throughout is defined in
[CONTEXT.md](CONTEXT.md); the decisions behind the design are in [docs/adr](docs/adr) and the v1
scope is in [docs/spec](docs/spec).

## Quick start

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
cp .env.example .env          # set LLM_API_KEY (and LLM_BASE_URL for a non-OpenAI endpoint)
.venv/bin/uvicorn app.main:app --port 8100
```

Open <http://localhost:8100> for a debug page: type a Task, watch the Trace and screenshots arrive.
The OpenAPI document is at `/docs`.

With Docker:

```bash
docker compose up --build              # service only
docker compose --profile search up     # service plus a local SearXNG
```

## Driving it as a Toolset

The MCP endpoint is `http://localhost:8100/mcp/mcp` (Streamable HTTP). Every session-scoped tool
takes the `session_id` that `open_session` returns, so one connection can drive several Sessions.

```
open_session                       -> {"session_id": "..."}
navigate(session_id, url)          -> a Snapshot of the page
click / type / select_option / hover / press_key / scroll / submit
snapshot / screenshot / get_text / find
list_tabs / switch_tab / close_tab
search(query)                      -> only when SEARXNG_URL is set
close_session(session_id)
```

A Snapshot is what the model reads:

```
# url=https://example.test/contact | title="Contact" | tab=1/1 | scroll=0%
- heading "Contact us" [level=1]
- form:
  - textbox "Full name" [ref=e3]
  - textbox "Email" [ref=e4] [value="ada@example.test"]
  - combobox "Topic" [ref=e5] [value="Sales"] [options=sales,support]
  - button "Send message" [ref=e6] [new]
```

Act on elements by `ref`. Refs come from the newest Snapshot only — acting on an older one is
refused rather than guessed at, and `[new]` marks what appeared since the last Snapshot.

## Driving it as a Run

```bash
curl -s localhost:8100/runs -H 'content-type: application/json' -d '{
  "task": "On example.test, find the support email address",
  "output_schema": {"type":"object","properties":{"email":{"type":"string"}},"required":["email"]},
  "autonomy": "confirm_risky"
}'
# {"run_id":"...","status":"running"}

curl -N localhost:8100/runs/<id>/events    # the Trace, as server-sent events
curl -s  localhost:8100/runs/<id>          # result, outcome, sources, limits used
curl -s -XPOST localhost:8100/runs/<id>/confirm -d '{"decision":"approve"}'
curl -s -XPOST localhost:8100/runs/<id>/abort
```

A Run ends **completed**, **failed**, **aborted** or **blocked** — the last meaning a captcha or
login wall it will not try to defeat.

### Autonomy and risky actions

`autonomous` (the default) never pauses. `confirm_risky` pauses before each Risky Action and emits
`confirmation.requested`; answer with `/confirm` or the Run aborts when the confirmation times out.
A Risky Action is detected three ways, and is always tagged in the Trace even when nothing pauses:

- the agent declares it by calling `submit(ref, purpose)` instead of `click`;
- the click structurally submits a form;
- the url matches one of the caller's `risky_url_patterns`.

### Safety

Every request the browser makes — page loads, sub-resources, redirects, whatever JavaScript starts —
is checked before it leaves: private, loopback, link-local and metadata addresses are refused, and
so is anything the operator-wide blocked-domain list or the per-Run allow/block lists exclude. The
check sits on the browser context rather than on the navigate Action, so a link or a script cannot
route around it. Navigations to the same host are spaced out. The browser presents itself as an ordinary Chrome; it
does not spoof fingerprints and does not try to beat captchas. The agent cannot execute JavaScript
on a page, so hostile page text cannot escalate into code execution.

## Configuration

Everything is environment-driven — see [.env.example](.env.example). Callers may override the
model, budgets and limits per Run; operator caps clamp them, and the record of each Run reports the
values it actually ran with.

## Tests

```bash
.venv/bin/python -m pytest tests/unit          # pure functions, no browser
.venv/bin/python -m pytest tests/integration   # real Chromium against an in-process fixture site
.venv/bin/python -m pytest                     # both
```

Integration tests drive the real ASGI app over HTTP with a fake OpenAI-compatible endpoint that
replays scripted turns, so Runs are deterministic. `evals/` holds real-web tasks for measuring how
often the agent actually succeeds; they need a real model and are not part of the suite — see
[evals/README.md](evals/README.md).

## What v1 does not do

Logged-in sites and per-user browser profiles, file downloads and uploads, PDF rendering,
drag-and-drop, arbitrary JavaScript, captcha solving, multi-replica deployment, Prometheus or
OpenTelemetry. The seams for the first two are in place; the rest are deliberate omissions.
