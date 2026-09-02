# Open Cloud Assistant Roadmap

This roadmap tracks the validated public project from its historical v0.1.0 prerelease through the current v0.3.0 engineering baseline. Historical checklist entries intentionally retain their original release labels.

## Phase 1 — Public repository foundation

- [x] Separate public source from private production state
- [x] Public project identity and MIT license
- [x] `.gitignore` and configuration-key reference
- [x] Third-party notices and exact upstream license copies
- [x] Public credential/privacy audit
- [x] Smoke-test harness
- [x] Security policy and contributing guide
- [x] Public GitHub repository, `main`, and v0.1.0 prerelease

## Phase 2 — Installation and diagnostics

- [x] Implement `setup.sh --dry-run`
- [x] Implement `setup.sh --install`
- [x] Implement doctor with PASS / FAIL / SKIP semantics
- [x] ARM64 preflight checks
- [x] x86_64 preflight acceptance
- [x] Idempotent second installation proof on the ARM64 release path
- [x] Validated backup/rollback boundaries for live Hermes integration and self-repair
- [ ] Automatically bootstrap all Ubuntu OS prerequisites
- [ ] Supported uninstall command

## Phase 3 — Core integrations

- [x] Hermes installer
- [x] Vellum installer
- [x] Hermes ↔ Vellum deterministic context bridge
- [x] Dynamic Hermes Fleet runtime/registry
- [x] Parallel-worker configuration
- [x] Free-first provider policy
- [x] Restricted OpenCode repair workflow
- [x] Optional cross-platform channel wizard
- [x] Always-on systemd user-service layer
- [ ] Public reusable Vellum Fleet configuration equivalent to the dynamic Hermes Fleet layer

## Phase 4 — Documentation

- [x] Documentation index
- [x] Complete zero-to-running setup guide
- [x] Architecture guide
- [x] Oracle Cloud guide
- [x] Generic Ubuntu/VPS guide
- [x] Provider configuration guide
- [x] Channel guide
- [x] Hermes/Vellum integration guide
- [x] Fleet guides
- [x] Parallel-worker architecture documentation
- [x] Self-repair guide
- [x] Security policy
- [x] Operations/update guide
- [x] Troubleshooting guide
- [ ] Dedicated Tailscale/private-networking guide
- [ ] Dedicated Photon/iMessage setup guide
- [ ] Supported backup/restore guide backed by a project command

## Phase 5 — Reproducibility and acceptance

- [x] Fresh Ubuntu 24.04 ARM64 installation with documented base prerequisites
- [x] Complete second-run ARM64 install proof
- [x] Core doctor PASS proof on clean ARM64 CLI path
- [x] Public privacy/credential audit proof
- [x] Hermes↔Vellum integration smoke test
- [x] Parallel-worker configuration smoke test
- [x] Controlled self-repair smoke test
- [x] Rollback behavior smoke test
- [ ] Fresh Ubuntu x86_64 end-to-end proof
- [ ] Fresh-machine proof with user-supplied live provider credentials and a real model conversation
- [ ] Telegram real end-to-end test
- [ ] Discord real end-to-end test
- [ ] Browser/Open WebUI real end-to-end test

## Phase 6 — Stable release

- [x] Third-party license audit for v0.1.0
- [x] Credential/privacy/history scan for v0.1.0
- [x] Create and publish public GitHub repository
- [x] Publish first prerelease
- [x] Tag `v0.1.0`
- [ ] Close all stable channel acceptance gates
- [ ] Complete x86_64 support claim or document ARM64-only stable support
- [ ] Add prerequisite bootstrap or make the manual prerequisite contract a permanent supported design
- [ ] Publish stable `v1.0.0`

## v1.0 release standard

Open Cloud Assistant reaches stable v1.0 only when a new user can:

1. start from a clean supported Ubuntu host;
2. follow the public guide without private deployment knowledge;
3. install dependencies/core integrations reproducibly;
4. provide their own provider and optional channel credentials;
5. pass `opencloud doctor`/`./bin/opencloud doctor` for the selected supported configuration;
6. hold a real assistant conversation;
7. use personal context and parallel orchestration without exposing internal machinery;
8. keep the selected messaging path running across logout/reboot;
9. recover safely from an install/repair failure;
10. do all of this without manually editing installed production source.

## Cross-platform conversation acceptance

- [x] Core installation succeeds without Apple hardware
- [ ] Telegram guided setup + real message/response proof
- [ ] Discord guided setup + real message/response proof
- [ ] Browser/Open WebUI guided setup + real browser conversation proof
- [ ] Fresh public CLI conversation with user-supplied live provider credentials
- [x] iMessage remains optional
- [x] Apple-specific questions appear only when iMessage is selected
- [x] Doctor reports unconfigured optional channels as SKIP, not FAIL


## 2026 engineering completion gate

Completed engineering milestones:

- [x] GitHub Actions CI on Ubuntu x86_64 and ARM64 runners
- [x] deterministic Fleet failure injection and cooldown recovery
- [x] bounded Hermes concurrency proof
- [x] self-repair staged validation and rollback fault injection
- [x] service persistence configuration and controlled recovery
- [x] automatic Ubuntu prerequisite bootstrap
- [x] safe ownership-conscious uninstall command
- [x] runtime-integrity doctor checks
- [x] reproducible release-check command

Validation scope:

- ARM64 Ubuntu has a real clean-install and idempotency proof.
- x86_64 source and hosted-CI compatibility are validated.
- real x86_64 machine acceptance is currently deferred and must not be
  represented as completed.
- optional messaging adapters must publish their actual validation status.
- browser integration remains preview until separately validated.

Before creating a stable tag, run:

    ./bin/opencloud release check

A stable tag must not be created when that command fails.

## v0.2.0 release

Completed:

- [x] GitHub Actions CI
- [x] Fleet fault injection
- [x] Hermes concurrency reliability
- [x] self-repair rollback reliability
- [x] service persistence and controlled recovery
- [x] prerequisite bootstrap
- [x] safe uninstall
- [x] runtime-integrity doctor
- [x] reproducible release gate
- [x] release checklist
- [x] changelog and release notes

Deferred without blocking v0.2.0:

- [ ] real x86_64 machine acceptance
- [ ] optional-channel end-to-end expansion
- [ ] browser graduation from preview

Future feature development belongs to post-v0.3.0 work.

## v0.3.0 engineering baseline

Completed after the immutable v0.2.0 release:

- [x] OS-level Bubblewrap sandbox around the self-repair AI editing process
- [x] Ubuntu AppArmor user-namespace integration without globally disabling the restriction
- [x] deterministic sandbox filesystem/write-boundary acceptance
- [x] existing trusted backup and rollback regression retained
- [x] OCI Terraform root module
- [x] Terraform provider lock for Linux amd64 and arm64
- [x] hosted Terraform validation on x86_64 and ARM64
- [x] sanitized real ARM64 operational evidence
- [x] longitudinal evidence collector with privacy guards
- [x] semantic CLI version reporting

Current explicit limitations remain:

- real x86_64 machine acceptance is deferred;
- Telegram and Discord public end-to-end acceptance is pending;
- Browser/Open WebUI remains preview;
- Gemini remains ineligible until dynamically discovered and freshly verified.
