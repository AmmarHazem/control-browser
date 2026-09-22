# The browser is a Toolset; the autonomous Run is a layer on top of it

The service exposes stateful Session tools over MCP (and REST for administration) so that an external agent runtime — the caller's own agent brain — can interleave browser Steps with its other tools. The convenience `POST /runs` endpoint, which drives an OpenAI-Agents-SDK loop against that same Toolset until a Task is done, exists for callers without an agent and for testing, not as the primary integration. The alternative — a loop-owning browser agent like the existing `/research` endpoint in the sibling web-search-service — is simpler to call but creates a second agent runtime to maintain and forbids the calling agent from mixing browser Actions with anything else.

## Consequences

- Every capability must be reachable as a tool with an explicit `session_id`; the Run loop is not allowed to use private shortcuts the Toolset lacks.
- Confirmation, Autonomy Levels and Risky Action detection live in the policy layer under the Toolset so both paths enforce them identically.
- If the Run loop ever moves into an external agent runtime, the Toolset is what carries over unchanged.
