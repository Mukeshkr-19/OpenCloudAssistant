# Complete Installation Pipeline

Validate the complete installation path with:

    ./setup.sh --dry-run

Install with:

    ./setup.sh --install

## Hermes integration

install/30-brain-materialize.sh remains the non-mutating upstream compatibility validator.

install/35-hermes-live.sh is the dedicated live installer.

It materializes the public patches against a clean Hermes HEAD, validates the resulting
tree, creates a local backup, copies only the validated integration files, and restores
the backup if live validation fails.

A second valid installation returns ALREADY_PRESENT instead of applying patches again.

## Complete installation

The setup pipeline covers Hermes, Vellum, restricted self-repair, dynamic Fleet,
the Hermes-to-Vellum bridge, private task-profile support, parallel
orchestration, channels, always-on services, and the final doctor.

install/40-context-materialize.sh remains a compatibility validator because the full
Vellum MCP server is installed by install/80-vellum-bridge.sh.

install/50-workers.sh remains the canonical policy validator because actual Hermes
orchestration configuration is applied by install/85-hermes-orchestration.sh.

Private task profiles remain under `~/.opencloud/task-profiles` and are not
materialized from the public repository. `opencloud task-profile apply --name
NAME` applies one to a matching existing Hermes profile.

## Fresh-host dry-run semantics

On a fresh host, Hermes may not exist yet when `./setup.sh --dry-run` runs.

Repository-local validation still runs immediately. Checks that require an
installed Hermes source tree are reported as deferred until Hermes installation.

This deferral applies only to the non-mutating pre-install dry-run.

`./setup.sh --install` remains strict: Hermes is installed first and the real
compatibility and live-integration checks must pass before installation continues.

CI separately validates the captured supported Hermes baseline so deferred
fresh-host checks do not remove upstream compatibility coverage.

## Channels

Interactive installs launch the channel wizard when no saved selection exists.

Noninteractive installs default to CLI unless OPEN_CLOUD_CHANNELS is provided.

Examples:

    OPEN_CLOUD_CHANNELS=cli ./setup.sh --install

    OPEN_CLOUD_CHANNELS=telegram,cli ./setup.sh --install

Fresh Ubuntu installation and complete second-run validation remain release gates.
