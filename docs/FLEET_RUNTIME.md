# Fleet Runtime

Open Cloud Assistant separates model runtime machinery, provider policy, and
provider permission gating.

## Dispatcher

The Fleet dispatcher is stored at:

    integrations/fleet/dispatcher.py

It handles runtime discovery, candidate selection, provider state, failure
handling, cooldown behavior, health tracking, and SQLite-backed local state.

The dispatcher may discover Gemini configuration. Discovery does not mean that
Gemini is permitted for routing.

## Provider policy

The permanent public Fleet policy is stored at:

    config/fleet/hermes-fleet-policy.json

The stable OpenRouter fallback route is:

    openrouter/free

Concrete NVIDIA and Zen model identifiers are discovered dynamically and are
not permanently committed.

## Gemini safety

Gemini permission gating is enforced by the Hermes Fleet integration rather
than by the generic dispatcher.

The public Hermes integration must retain:

    HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1

Until that lane is independently verified, discovery alone must not make it an
allowed fallback.

## Runtime-only state

Discovered model registries, provider-health databases, cooldown state,
selection state, native proof files, session state, and credentials remain
local runtime data and must not be committed to Git.
