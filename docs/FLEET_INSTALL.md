# Fleet Installation

Open Cloud Assistant installs Hermes Fleet runtime state under:

    ~/.local/share/hermes-fleet

The runtime owns:

- dispatcher.py
- fleet.json
- registry/models.json
- health.sqlite

The public repository owns the sanitized dispatcher source and permanent Fleet policy.

## Fresh installation

A fresh installation may begin with an empty dynamic registry.

That is valid. Dynamic NVIDIA and optional Zen candidates are added later by the
registry refresher. Until discovery succeeds, the stable free OpenRouter route may
provide the eligible fallback defined by policy.

## Runtime state

Existing registry data and health databases are deployment state and must not be
copied into Git or overwritten by repository validation.

## Commands

    opencloud fleet paths
    opencloud fleet status
    opencloud fleet select worker

## Provider configuration

NVIDIA and OpenRouter credentials are configured separately from source installation.
OpenCode Zen remains optional and dynamically discovered.

Gemini remains disabled until independently verified.
