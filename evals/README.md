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
