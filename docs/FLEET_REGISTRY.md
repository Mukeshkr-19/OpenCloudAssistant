# Dynamic Fleet Registry

Open Cloud Assistant uses runtime model discovery rather than permanent model IDs.

## Canonical runtime state

The canonical continuously maintained registry is:

    ~/.local/share/hermes-fleet/registry/models.json

The refresh process discovers providers and writes providerStatus, model candidates,
productionModels, and quarantineModels.

The verification process updates compatibility state, production eligibility, and
lastVerificationRunMs in the same registry.

Successful compatibility is fresh for 24 hours by default. After that, a model
becomes eligible for another synthetic tool-call probe; successful probes refresh
`verifiedAtMs`, while failures remove production eligibility. Override the interval
with `OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS` in `~/.opencloud/config.env`.
The value must be an integer from `0` through `31536000` seconds. `0` disables
freshness caching, making every verified model eligible for re-probe on each
verifier run; negative, malformed, and larger values fail with a concise
configuration error rather than a traceback.

All Fleet components resolve their root from `OPEN_CLOUD_FLEET_HOME`, defaulting
to `~/.local/share/hermes-fleet`. The dispatcher, registry workers, session-pin
key, Hermes bridge, doctor, and systemd rendering share that policy.
Discovery freshness, provider cooldowns, quarantine, and capability verification
remain separate states. Refresh and verification serialize through `registry.lock`.

## Native proof

A native-proof.json file may exist on an already-proven deployment, but it is not
produced by the periodic refresh or verifier services and is therefore not required
by the public runtime.

Open Cloud Assistant doctor and fleet proof use models.json instead.

## Commands

    opencloud providers status
    opencloud providers configure
    opencloud fleet refresh
    opencloud fleet verify
    opencloud fleet proof

## Provider policy

NVIDIA uses dynamically discovered and verified production capacity.

OpenCode Zen is optional and uses dynamically discovered verified free capacity.

OpenRouter retains the stable policy route:

    openrouter/free

Gemini remains blocked until independently verified.

## Credentials

Provider credentials are stored locally at:

    ~/.opencloud/config.env

The file is mode 600.

Only NVIDIA_API_KEY and OPENROUTER_API_KEY are exported by the Fleet refresh wrapper.
Gemini is intentionally not enabled by this workflow.
