# Contributing to Open Cloud Assistant

Thanks for helping improve Open Cloud Assistant.

## Design principles

Contributions should preserve these architectural rules:

1. Hermes remains the primary conversational orchestrator.
2. Personal memory remains a separate context layer.
3. Parallel workers are temporary execution units, not permanent personalities.
4. Provider/model routing remains dynamic rather than permanently hard-coding temporary model IDs.
5. A free-first path should be preserved when practical.
6. Private user context must not be committed to source control.
7. Coding agents must not silently gain broad filesystem, secret, Git, or service-control privileges.
8. User-facing conversations should not expose unnecessary internal failover or debugging chatter.
9. Behavioral changes should include validation or tests.

## Development workflow

Before opening a pull request, run:

    ./scripts/public-audit.sh
    ./tests/smoke/run.sh

When the installer and integration tests are available, run those as well.

## Pull requests

Keep changes focused and explain:

- what changed,
- why it changed,
- how it was tested,
- whether it changes permissions, providers, model routing, memory behavior, or deployment assumptions.

Never include:

- real API keys,
- `.env` files,
- private memory,
- production databases,
- authentication state,
- private logs,
- personal conversations.

## Third-party code

Do not copy third-party source into this repository unless redistribution is necessary and license-compliant.

Prefer official upstream installers or package sources and preserve required attribution.
