# Public Brain Integration References

This directory set contains sanitized integration references derived from a
proven Open Cloud Assistant deployment.

These files are not private runtime state.

## Included

- dynamic Hermes Fleet policy
- Hermes Fleet integration patch
- Hermes live orchestration integration patch
- deterministic Hermes-to-Vellum context bridge blocks
- compatibility notes

## Portability

Private absolute home paths are replaced with:

    __OPEN_CLOUD_HOME__

The installer must render that placeholder to the target users home directory
when materializing an installation.

## Model policy

Concrete NVIDIA and dynamic provider model IDs are intentionally not part of
the permanent public architecture.

Runtime provider discovery chooses currently available candidates.

The stable OpenRouter free route may remain a provider fallback policy.

## Personal data

These files must never contain:

- Vellum personal memories
- conversations
- API credentials
- session identifiers
- Fleet runtime databases
- private provider registries
- private hostnames or addresses

## Self-repair

The private deployment self-repair harness is intentionally not imported by
this stage because its trusted private backup/checkpoint integration must be
replaced with a generic public-safe outer workflow.
