# Evals

Real-web Tasks with expected outcomes, used to measure how often a Run actually succeeds as the
prompts and the Snapshot format change. They need a real model and live network, cost money, and
are deliberately **not** part of the test suite: they measure the agent, not the code.

```bash
export LLM_API_KEY=...            # and LLM_BASE_URL for a non-OpenAI endpoint
.venv/bin/uvicorn app.main:app --port 8100 &
.venv/bin/python evals/run_evals.py --base-url http://localhost:8100 --timeout 180
```

`--only <name>` runs a single case. The runner reports a success rate and prints each case's Trace
id, so a failure can be replayed at `/runs/<id>/events`.

## Writing a case

Each case in `cases.json` is a Task, an optional `output_schema`, and an `expect` block:

| Field | Meaning |
|---|---|
| `outcome` | The expected Outcome, or a list of acceptable ones. |
| `contains` | Substrings that must appear in the result, matched case-insensitively. |
| `not_contains` | Substrings that must **not** appear — the way to assert an agent did not invent something. |

## Reading the results

A case can fail for two quite different reasons, and they need different responses:

- **The agent did the wrong thing.** Replay the Trace and look at what the model saw before it went
  wrong. This is the signal the suite exists for.
- **The case was wrong.** Real sites change, and an expectation can encode an assumption the web
  does not honour. Fix the case.

Judge the pass rate against a previous run, never in isolation. A single number says nothing; a
number that dropped after a prompt change says a lot.

Watch for a case that passes for the wrong reason. A Run that answers a well-known fact in two
steps may have read the page or may be reciting from training data — check `sources` and the Trace
before believing it. Cases that ask for something only the live page can tell you (a headline, a
form's echo of what was submitted) are worth more than cases a model could answer offline.

## First run, 2026-09-26

`gpt-5.4-mini`, `max_steps=25`: **10/10** after two cases were recalibrated, most finishing in two
steps because the first Snapshot already contained the answer.

Two cases were wrong rather than the agent:

- **`multi-page-comparison`** asked for package licences from pypi.org, which served a
  `Client Challenge` bot wall. The Run ended **blocked**, which is the designed behaviour — the
  agent is not meant to fight captchas — so the case now compares two Wikipedia pages instead and
  tests multi-page navigation without testing anti-bot.
- **`impossible-task`** expected the agent to `give_up` when asked for a bank balance on
  example.com. It instead reported plainly that the page holds no such information, which is a
  better answer than giving up. It is now **`absent-information`**, accepts either Outcome, and
  asserts through `not_contains` that no figure was invented — which is what the case was really
  guarding against.

## Real booking sites, 2026-10-02

`gpt-5.4-mini`, `max_steps=30`. Three sites a user reported failing the same way: the agent could
not see a dropdown the user could see opening in the browser.

The cause was in the snapshot collector, not the sites. `role="none"` and `role="presentation"`
mean "drop this element's own semantics and keep its children", but the collector dropped the
whole subtree. Component libraries wrap listbox and menu contents in exactly those elements, so
every suggestion list built that way rendered as an empty `listbox`. Three smaller visibility rules
had the same shape: a `display: contents` wrapper, a `visibility: hidden` parent with a visible
child, and an `opacity: 0` styled checkbox each took visible content down with them. Open shadow
roots were not walked at all.

| Case | Before | After |
|---|---|---|
| `wego-flight` | could not see the suggestions | **passes** — AED 486, Jazeera Airways, 18 steps |
| `wingie-flight` | could not see the suggestions | selects both cities through their autocompletes, then gives up inside the date picker |
| `vox-showtimes` | page never loaded | loads, finds the film, then the site refuses the next request |

**Vox is blocked deliberately.** Its first page loads; every later navigation fails with
`ERR_HTTP2_PROTOCOL_ERROR`, headless or headed, with or without the request guard. It is detecting
an automated Chrome, and getting past that would mean faking automation fingerprints, which this
service does not do. The case is left strict so it stays visible as a known failure.

**Wingie's date picker is the open problem.** Trace events keep only the first 2,000 characters of
each observation, and the calendar renders at the end of the page, so the stored Trace does not
show what the model saw. Raise that limit before debugging it further.
