# Release Checklist

## Source and security

- [x] Public audit passes.
- [x] Shell and Python syntax validation passes.
- [x] GitHub Actions workflow YAML parses correctly.
- [x] Third-party license and compatibility metadata are present.
- [x] No committed credentials or runtime secrets.

## Installation

- [x] Ubuntu prerequisite detection works.
- [x] Missing prerequisites can be installed automatically.
- [x] Dry-run does not install packages.
- [x] ARM64 clean-machine installation validated.
- [x] Installer idempotency validated.
- [x] Clean-HOME dry-run validated.

## Runtime

- [x] Runtime-integrity doctor.
- [x] Fleet state validation.
- [x] Hermes and Vellum bridge validation.
- [x] Hermes orchestration validation.
- [x] Fleet systemd timers.
- [x] User lingering for boot persistence.

## Reliability

- [x] Candidate isolation.
- [x] Rate-limit failover.
- [x] Server-error failover.
- [x] Network failover.
- [x] Cooldown recovery.
- [x] Hermes three-worker concurrency proof.
- [x] Invalid self-repair rejection.
- [x] Trusted repair backup.
- [x] Repair rollback execution.
- [x] Controlled service recovery.

## Lifecycle

- [x] Safe uninstall plan.
- [x] Ownership-aware uninstall behavior.
- [x] Personal data retained by default.
- [x] Runtime-integrity doctor.
- [x] Release-check command.

## Channels

- [x] CLI core path validated.
- [x] Optional channels do not block the core release.
- [x] Unvalidated adapters are not represented as end-to-end validated.
- [x] Browser remains preview.

## Deferred acceptance

- [ ] Real x86_64 machine acceptance.

Real x86_64 machine acceptance is non-blocking for v0.2.0.
Hosted x86_64 CI and source compatibility are validated.
A dedicated real-machine acceptance harness exists for future execution.

## Final release command

    ./bin/opencloud release check

A release tag must not be created when this command fails.
