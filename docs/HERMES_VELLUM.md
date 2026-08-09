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
    max_concurrent_children = 3
    max_iterations = 50
    max_spawn_depth = 1
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
