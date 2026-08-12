# Hermes and Vellum Integration

Open Cloud Assistant uses Hermes as the user-facing orchestrator and Vellum as the personal-context brain.

Hermes can request deterministic user context through the local vellum-bridge MCP server.

The bridge exposes get_user_context and the existing bounded Vellum task tools.

Personal memory and Vellum runtime state are never stored in this repository.

## Hermes MCP configuration

The installer configures:

    mcp_servers.vellum-bridge.enabled = true
    mcp_servers.vellum-bridge.connect_timeout = 30

The MCP server runs through the local Hermes Python environment and receives server.py as its argument.

## Orchestration

The canonical public policy explicitly configures:

    orchestrator_enabled = true
    max_concurrent_children = 4
    max_iterations = 12
    child_timeout_seconds = 120
    max_spawn_depth = 2
    inherit_mcp_toolsets = true

The installer does not replace unrelated delegation configuration such as user-defined provider or model settings.

The installer verifies that the installed Hermes source supports each orchestration key before changing config.

## Privacy

The public bridge contains code only.

User memory, MCP task state, credentials, prompts, runtime databases, and personal context remain outside Git.

## Installation stages

    install/80-vellum-bridge.sh --install
    install/85-hermes-orchestration.sh --install

Both stages also provide non-mutating --check modes.

## Read/write routing contract

Open Cloud Assistant separates personal-context reads from personal-memory
mutations.

For ordinary reads about the user, Hermes uses get_user_context. It may make
one narrower retry when retrieved material is weak, and it ignores generic
skill descriptions or capability documentation that are not user facts.

For explicit remember, save, update, correct, or forget requests, Hermes uses
start_vellum_task once and polls the same task with get_vellum_task until the
mutation completes or fails.

The bridge installer places both `server.py` and `worker.py` under
`~/.config/hermes-vellum/mcp/`. Mutation tasks launch the worker with the same
Python interpreter that is running the MCP server; no separately named or
privately provisioned Vellum virtual environment is required. The worker uses
the supported `vellum message` command and stores
only bounded task state under the private bridge directory.

The asynchronous task path is not required for ordinary personal-context
retrieval.

Normal user-facing responses must not expose MCP metadata, task IDs, raw JSON,
model routing, stack traces, or internal retrieval diagnostics.

Restrictive private task profiles can allow only `get_user_context` from the
Vellum server. Delegated children inherit the parent's MCP surface by
intersection and cannot regain other Vellum tools.

## Gateway lifecycle notifications

Routine Hermes gateway shutdown and restart messages are internal lifecycle
events and are suppressed from normal user conversations by default.

The underlying shutdown interrupt, recovery state, service logs, and messaging
adapter reconnect behavior remain unchanged.

Operators can opt back into the upstream notices with
`HERMES_GATEWAY_LIFECYCLE_NOTICES=1`.
