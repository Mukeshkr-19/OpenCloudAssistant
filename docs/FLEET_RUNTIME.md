# Fleet Runtime

Open Cloud Assistant separates model runtime machinery, provider policy, and
provider permission gating.

## Dispatcher

The Fleet dispatcher is stored at:

    integrations/fleet/dispatcher.py

It handles runtime discovery, candidate selection, provider state, failure
handling, cooldown behavior, health tracking, and SQLite-backed local state.

The dispatcher discovers provider catalogs from policy. A model is routable
only after fresh verification and while it is healthy and outside cooldown.

## Provider policy

The permanent public Fleet policy is stored at:

    config/fleet/hermes-fleet-policy.json

The stable OpenRouter fallback route is:

    openrouter/free

Concrete NVIDIA, Zen, OpenRouter, and Gemini model identifiers are discovered dynamically and are
not permanently committed.

## Gemini quota conservation

Gemini uses the same fresh-verification and cooldown gates as other dynamic
providers. Its pool has a provider-level automatic ranking penalty so verified
free routes are conserved for later fallback; an explicit session pin still
wins until the user returns the session to AUTO.

## Runtime-only state

Discovered model registries, provider-health databases, cooldown state,
selection state, native proof files, session state, and credentials remain
local runtime data and must not be committed to Git.
