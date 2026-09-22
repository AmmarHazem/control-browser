# Evals

Real-web Tasks with expected outcomes, used to measure how often a Run actually succeeds as the
prompts and the Snapshot format change. They need a real model and live network, cost money, and
are deliberately **not** part of the test suite: they measure the agent, not the code.

```bash
export LLM_API_KEY=...            # and LLM_BASE_URL for a non-OpenAI endpoint
.venv/bin/uvicorn app.main:app --port 8100 &
.venv/bin/python evals/run_evals.py --base-url http://localhost:8100
```

Each case in `cases.json` is a Task, an optional output schema, and a `expect` block: substrings
that must appear in the result, and the Outcome expected. The runner reports a success rate and
writes the per-case Trace ids so failures can be replayed at `/runs/<id>/events`.
