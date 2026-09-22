# Browser Agent

A service that lets an LLM operate a web browser the way a person does: open pages, read them, click, type, submit forms and carry state from one step to the next. Consumed by developers and other services through an API, with any OpenAI-compatible model.

## Language

### Browsing

**Session**:
One isolated, stateful browser instance (its own cookies, storage and tabs) that lives from open to close and carries state across Actions.
_Avoid_: Browser, context, tab (a Session may hold several tabs)

**Action**:
A single browser operation the LLM requests inside a Session, such as navigate, click, type, select, scroll or go back.
_Avoid_: Command, step, tool call

**Observation**:
What the LLM is shown after an Action: the page's Snapshot and, on demand, a Screenshot.
_Avoid_: State, page content

**Snapshot**:
A compact text outline of the current page listing interactive elements with a Ref each; the default way the LLM perceives a page.
_Avoid_: Accessibility tree, DOM dump, element list

**Screenshot**:
A rendered image of the current viewport, requested only when the Snapshot is insufficient or a vision-capable model wants to look.

**Ref**:
The short stable identifier attached to an element in a Snapshot that an Action targets instead of a coordinate or CSS selector.
_Avoid_: Selector, index, element id

### Agency

**Toolset**:
The set of Session-scoped Actions and Observations exposed to an external agent that runs its own reasoning loop. The primitive the service is built on.
_Avoid_: API, tools, MCP server

**Run**:
An autonomous execution in which the service itself drives the LLM loop against the Toolset until a Task is done or stopped.
_Avoid_: Job, execution, agent run

**Task**:
The natural-language goal a caller hands to a Run.
_Avoid_: Prompt, query, instruction

**Step**:
One observe → decide → act cycle inside a Run: the LLM reads an Observation, chooses an Action, and receives its result.
_Avoid_: Turn, iteration

**Trace**:
The ordered record of everything that happened in a Run — its Steps, Actions, Observations and outcome — streamed live and kept afterwards.
_Avoid_: Log, history, event stream

**Sources**:
The URLs a Run visited, always returned alongside its result.

**Outcome**:
How a Run ended: Completed (goal reached), Failed (error or limits hit), Aborted (caller stopped it) or Blocked (a wall the agent will not try to beat, such as a captcha or login gate).

**Autonomy Level**:
The caller-chosen policy on a Run that says which Actions may proceed without Confirmation. Two levels exist: `autonomous` (never pause; the default) and `confirm_risky` (pause before each Risky Action).
_Avoid_: Mode, permission level, safety level

**Risky Action**:
An Action whose effect is hard to undo outside the browser — submitting an order, sending a message, deleting data. Gated by the Autonomy Level.
_Avoid_: Dangerous action, destructive action, write action

**Confirmation**:
The pause a Run enters before a Risky Action, awaiting a caller's decision to proceed or abort.
_Avoid_: Approval, human-in-the-loop, interrupt
