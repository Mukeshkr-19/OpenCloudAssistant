# Brain Materialization

Open Cloud Assistant stores sanitized Hermes, Fleet, and Vellum integration
references in the public repository.

The materialization layer converts those references into deployment-ready
files for a target installation.

## Goals

The materialization stage is responsible for:

- validating the Hermes Fleet integration references;
- rendering portable home-directory placeholders;
- validating the Hermes orchestration integration;
- preparing the deterministic Vellum context bridge;
- validating the parallel-worker policy;
- preserving dynamic runtime model discovery;
- preventing private deployment data from entering the public install.

## Hermes

The Hermes materializer works from the sanitized integration files under:

    integrations/hermes/

Public integration references use the portable placeholder:

    __OPEN_CLOUD_HOME__

The installation layer renders that placeholder for the target user.

The public architecture must not permanently hard-code discovered NVIDIA,
OpenCode Zen, or other runtime model identifiers.

## Vellum context

The Vellum integration provides deterministic user-context retrieval for
Hermes.

The public materializer validates the context bridge source and prepares it
for installation without including any personal memory database or user data.

Personal memory remains runtime data and must never be committed to this
repository.

## Parallel workers

The canonical orchestration policy is stored in:

    config/hermes/orchestration.json

The initial public policy requires:

- orchestration enabled;
- up to four concurrent child workers;
- maximum spawn depth of two;
- twelve iterations and a 120-second timeout per child;
- MCP toolset inheritance for workers;
- availability of the Vellum bridge when personal context is required.

Child workers are execution workers spawned for parts of a complex task.

Private task profiles are intentionally not materialized by the installer and
are retained by uninstall and public repository upgrades.
They are not permanent domain-specific agents.

## Safety during development

The materialization checks used while developing this repository do not write
to the running Hermes installation.

Actual deployment writes are enabled only through an explicit installer mode
after the installation workflow has completed its validation stages.

## Validation

The repository includes:

    tests/smoke/materialization.sh

The smoke test verifies the public materialization contract without modifying
a production assistant.
