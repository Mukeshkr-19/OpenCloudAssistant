# Changelog

## [0.3.0] - 2026-08-11

### Added

- Bubblewrap/AppArmor OS isolation for the restricted self-repair editing stage.
- Deterministic real-host self-repair sandbox acceptance evidence.
- OCI Terraform infrastructure-as-code with amd64 and arm64 provider locking.
- Hosted Terraform validation on x86_64 and ARM64.
- Sanitized operational evidence snapshots and longitudinal history collector.
- Semantic CLI version reporting.

### Changed

- Active documentation now reflects the current release behavior instead of
  the historical v0.1.0 prerelease state.
- Automatic Ubuntu prerequisite bootstrap documentation is aligned with the
  implemented installer behavior.

### Security

- Self-repair keeps the live target masked from the sandbox and confines
  controlled host-backed writes to staging and ephemeral sandbox HOME.
- Ubuntu AppArmor user-namespace restrictions remain enabled.
- Operational evidence excludes raw logs, credentials, personal memory,
  prompts, IP addresses, and session identifiers.

### Validation

- Public audit passes.
- Release gate passes.
- Hosted x86_64 and ARM64 smoke checks pass.
- Deterministic reliability checks pass.
- Real ARM64 OS-sandbox acceptance passes.


## [0.2.0] - 2026-08-10

### Added

- Deterministic Fleet provider and candidate fault injection.
- Hermes bounded parallel-worker reliability proof.
- Self-repair staged-validation and rollback fault injection.
- Service persistence and controlled recovery validation.
- Automatic Ubuntu prerequisite bootstrap.
- Ownership-aware safe uninstall lifecycle.
- Runtime-integrity doctor checks.
- Reproducible release gate.
- Real x86_64 host acceptance harness for future validation.

### Reliability

- Candidate failure isolation and recovery.
- Rate-limit failover and cooldown recovery.
- Server-error failover.
- Network-level provider failover and recovery.
- Three concurrent Hermes executor workers.
- Invalid staged repairs rejected before deployment.
- Trusted pre-deployment backup creation.
- Rollback after simulated deployment validation failure.
- Fleet timer persistence validation.
- Hermes gateway controlled restart recovery.

### Operations

    ./setup.sh --install
    ./bin/opencloud doctor
    ./bin/opencloud uninstall
    ./bin/opencloud uninstall --yes
    ./bin/opencloud release check

Safe uninstall preserves personal configuration, Hermes history, Vellum
memory, Fleet health history, and OpenCloud configuration by default.

### Validation scope

Validated:

- Ubuntu 24.04 ARM64 real clean-machine installation.
- ARM64 installation idempotency.
- Ubuntu x86_64 hosted-CI and source compatibility.
- CLI operator path.
- Dynamic Fleet reliability behavior.
- Hermes bounded parallel execution.
- Self-repair validation and rollback.
- Service persistence configuration and controlled recovery.
- Runtime-integrity doctor checks.
- Public-source security audit.

Deferred or preview:

- Real x86_64 machine acceptance.
- End-to-end validation of every optional messaging adapter.
- Browser integration remains preview until separately validated.

## [0.1.0]

Initial public prerelease.

The historical v0.1.0 tag remains unchanged.
