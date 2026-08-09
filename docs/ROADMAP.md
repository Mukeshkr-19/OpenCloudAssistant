# Open Cloud Assistant Roadmap

## Phase 1 — Public repository foundation

- [x] Separate public project from the private production repository
- [x] Create public project identity
- [x] Add `.gitignore`
- [x] Add `.env.example`
- [x] Add project MIT license
- [x] Add third-party notice framework
- [x] Add public credential/privacy audit
- [x] Add smoke-test harness
- [x] Add security policy
- [x] Add contributing guide
- [x] Create initial local Git checkpoint

## Phase 2 — Installation and diagnostics

- [ ] Implement `setup.sh`
- [ ] Implement `opencloud doctor`
- [ ] Ubuntu dependency bootstrap
- [ ] ARM64 checks
- [ ] x86_64 checks where supported
- [ ] idempotent installation
- [ ] safe rollback
- [ ] uninstall command

## Phase 3 — Core integrations

- [ ] Hermes installer
- [ ] Vellum installer
- [ ] Hermes ↔ Vellum deterministic context bridge
- [ ] dynamic Hermes Fleet
- [ ] dynamic Vellum Fleet
- [ ] parallel-worker configuration
- [ ] free-first provider configuration
- [ ] restricted OpenCode repair
- [ ] optional messaging integration

## Phase 4 — Documentation

- [ ] Architecture guide
- [ ] Oracle Cloud guide
- [ ] Generic Ubuntu/VPS guide
- [ ] Tailscale/networking guide
- [ ] Provider configuration guide
- [ ] Hermes guide
- [ ] Vellum guide
- [ ] Fleet guide
- [ ] Parallel-worker guide
- [ ] Photon/iMessage guide
- [ ] Self-repair guide
- [ ] Security guide
- [ ] Backup/restore guide
- [ ] Upgrade guide
- [ ] Troubleshooting guide

## Phase 5 — Reproducibility

- [ ] Fresh Ubuntu ARM64 installation
- [ ] Fresh Ubuntu x86_64 installation where supported
- [ ] Fresh configuration using user-supplied provider credentials
- [ ] `opencloud doctor` all-pass proof
- [ ] messaging smoke test
- [ ] personal-context smoke test
- [ ] parallel-worker smoke test
- [ ] controlled repair smoke test
- [ ] rollback smoke test

## Phase 6 — Public release

- [ ] Final third-party license audit
- [ ] Final credential/privacy scan
- [ ] Final documentation review
- [ ] Create GitHub repository `OpenCloudAssistant`
- [ ] Push `main`
- [ ] Publish first pre-release
- [ ] Tag `v1.0.0` only after fresh-machine reproducibility passes

## v1.0 release standard

Open Cloud Assistant reaches v1.0 only when a new user can:

1. start from a clean supported Linux host,
2. clone the repository,
3. run the documented installer,
4. provide their own credentials,
5. run `opencloud doctor`,
6. start the assistant,
7. use memory and worker orchestration,
8. recover safely from a failed setup or repair,
9. do all of this without manually editing installed production source.

## Cross-platform conversation acceptance

- [ ] Core installation succeeds without Apple hardware
- [ ] Telegram guided setup and end-to-end test
- [ ] Discord guided setup and end-to-end test
- [ ] Browser/Open WebUI guided setup and end-to-end test
- [ ] CLI conversation test
- [ ] iMessage remains optional
- [ ] Apple-specific questions appear only when iMessage is selected
- [ ] Doctor reports unconfigured optional channels as SKIP, not FAIL
