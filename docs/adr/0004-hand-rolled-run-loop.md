# The Run loop is hand-rolled over the openai client, not an agent framework

The v1 spec chose the OpenAI Agents SDK for the Run loop, matching a sibling service. Building it
showed three requirements the SDK does not serve, so the loop is ~250 lines over the bare `openai`
async client instead.

1. **Screenshots.** Tool results in the Agents SDK are strings; an image cannot be returned from a
   tool. Hybrid perception (ADR 0002) needs the picture to reach the model, so the loop appends it
   as its own user message after the screenshot tool returns — which requires owning the message
   list.
2. **Vision downgrade.** `vision: auto` must notice that the endpoint rejected image content, strip
   the images already in the conversation and carry on. That is a rewrite of the message history
   mid-run.
3. **Exact termination and Confirmation.** `done()` and `give_up()` decide the Outcome, and a tool
   may block for minutes awaiting a caller's Confirmation. Both are natural when the loop is yours
   and awkward when a framework owns the control flow and the error handling around tool calls.

## Consequences

- The only LLM dependency is the `openai` client, which is a protocol client, not a framework: any
  OpenAI-compatible endpoint works, which is the model-agnostic constraint this service was built
  under.
- Retries, tracing and token accounting are ours to write. The Trace covers what a caller needs;
  retries beyond the client's own are not implemented in v1.
- The tests exercise the real loop against a fake endpoint rather than mocking a framework, so the
  request the model actually receives — tools, messages, images — is asserted directly.
