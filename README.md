# Open Cloud Assistant

> A free-first, self-hosted, always-on personal AI assistant architecture.

**Open Cloud Assistant** is a public integration project for building an AI
assistant that can stay online, remember personal context, split complex work
across parallel workers, route across changing model providers, and perform
restricted automated code repair.

Created and maintained by **Mukesh Krishna Murthy**.

> **Status: pre-release.** The architecture is proven in a private deployment,
> but the public installer is still being converted into a clean,
> reproducible setup.

---

## What problem does this solve?

Most assistants are tied to one model, one app, or one machine.

Open Cloud Assistant is designed around independent layers:

- a main conversational orchestrator,
- temporary parallel workers,
- persistent personal memory,
- dynamic provider/model routing,
- failure handling,
- optional messaging,
- restricted code repair,
- and an always-on Linux host.

The user should be able to ask normally.

The assistant decides internally whether it needs memory, workers, tools,
verification, research, or code repair.

---

## Architecture

```text
                         USER
                           |
                           v
                   Messaging / CLI
                           |
                           v
                        HERMES
                  main orchestrator
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
          Worker A      Worker B      Worker C
          research      analysis      verification
             |             |             |
             +-------------+-------------+
                           |
                           v
                    Dynamic AI Fleet
                           |
                  free-first routing
                           |
                           v
                    Hermes synthesis
                           |
                           v
                         USER
```

Personal context is a separate path:

```text
Hermes
  |
  v
Vellum
  |
  v
relevant personal context
  |
  v
Hermes
```

Approved code repair is also separate:

```text
Hermes
  |
  v
repair_code()
  |
  v
snapshot
  |
  v
restricted OpenCode
  |
  +--> PASS --> retain
  |
  +--> FAIL --> rollback
```

---

## Core components

### Hermes Agent

Hermes is the primary user-facing assistant and orchestrator.

It is responsible for:

- conversation,
- planning,
- tools,
- child workers,
- model calls,
- messaging,
- and final synthesis.

The user should not need to manually say:

```text
spawn three agents
ask Vellum
use model X
delegate to worker Y
```

Those are internal orchestration decisions.

### Vellum Assistant

Vellum is used as the personal-memory and context layer.

The design intentionally keeps personal memory separate from the main
orchestrator. Hermes asks for user-specific context only when it is useful.

Private memory itself belongs to the user's runtime, not the source repo.

### Dynamic Fleet

The Fleet layer selects providers/models dynamically rather than making one
temporary model ID permanent architecture.

The design supports:

- provider discovery,
- candidate health,
- failure tracking,
- cooldown/quarantine,
- role-aware selection,
- and deterministic fallback.

### Parallel workers

Complex tasks can be divided into multiple independent child jobs and run
concurrently when that is useful.

Workers are temporary execution units, not permanent personalities.

### OpenCode repair

A restricted OpenCode workflow can be used as an automated coding mechanic.

The outer system controls:

- allowed targets,
- snapshots,
- validation,
- rollback,
- and privileged operations.

The coding agent should not receive unrestricted Git push or secret access.

### Messaging

Messaging is optional.

The reference architecture can use Hermes messaging integrations, including
iMessage through Photon where available and configured.

The core project should remain usable without requiring one specific chat
platform.

---

## Design principles

### Always-on

The reference deployment targets a continuously available Linux server.

### Self-hosted

Core orchestration and private runtime state remain under the user's control.

### Free-first

Routing can prefer available free provider/model lanes.

Free availability and quotas are controlled by external providers and can
change independently of this project.

### Dynamic

Temporary model IDs should not become permanent architecture.

### Private by design

Do not store personal memory, credentials, authentication state,
conversations, or runtime databases in source control.

### One clean answer

Internal worker status, model failover details, and debugging chatter belong
in logs, not normal user conversation.

---

## Target quick start

The v1.0 goal is:

```bash
git clone https://github.com/Mukeshkr-19/OpenCloudAssistant.git
cd OpenCloudAssistant
./setup.sh
opencloud doctor
```

A new user should provide their own:

- Linux/cloud host,
- provider credentials,
- optional messaging credentials,
- and any external account configuration they choose to enable.

The installer should handle the engineering setup.

---

## Planned installer flow

```text
supported Ubuntu host
        |
        v
./setup.sh
        |
        +--> dependency checks
        +--> Hermes install
        +--> Vellum install
        +--> provider config
        +--> Fleet integration
        +--> context bridge
        +--> optional messaging
        +--> self-repair tooling
        +--> system services
        |
        v
opencloud doctor
        |
        v
healthy assistant
```

The user should not have to reproduce the private development/debugging
history that created the reference system.

---

## Repository layout

```text
OpenCloudAssistant/
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── THIRD_PARTY_NOTICES.md
├── .env.example
├── setup.sh
│
├── config/
├── docs/
├── examples/
├── install/
├── integrations/
│   ├── hermes/
│   ├── vellum/
│   └── self-repair/
├── scripts/
├── tests/
└── third_party/
```

---

## Security model

This project treats Git as source control, not as a secret manager.

Never commit:

- API keys,
- bearer tokens,
- OAuth credentials,
- JWTs,
- SSH private keys,
- `.env` files,
- personal memory,
- private conversations,
- authentication state,
- runtime databases,
- session identifiers,
- or private production logs.

The repository includes a public-release audit that blocks common
credential-shaped values and forbidden runtime files.

See `SECURITY.md`.

---

## Upstream projects

Open Cloud Assistant is an independent integration project.

Major upstream projects currently include:

- **Hermes Agent** by Nous Research
- **Vellum Assistant** by Vellum AI
- **OpenCode** by the OpenCode contributors

Open Cloud Assistant does not claim ownership of those projects.

See `THIRD_PARTY_NOTICES.md`.

---

## Public-release standard

The repository is not v1.0 until a clean supported machine can:

1. clone the repository,
2. run the documented installer,
3. provide its own credentials,
4. pass `opencloud doctor`,
5. start the assistant,
6. recover or roll back safely when a setup stage fails,
7. and do all of that without manual production source edits.

Fresh-machine reproducibility is the release standard.

---

## Roadmap

The next milestones are:

- real `setup.sh`,
- `opencloud doctor`,
- Oracle Cloud guide,
- generic Ubuntu/VPS guide,
- provider configuration,
- Hermes installer,
- Vellum installer,
- Fleet installer,
- deterministic context bridge,
- worker configuration,
- optional Photon/iMessage integration,
- restricted OpenCode repair,
- backup/restore,
- upgrade workflow,
- clean ARM64 installation test,
- clean x86_64 installation test where supported,
- final license/secret audit,
- public GitHub release.

See `docs/ROADMAP.md`.

---

## Project identity

**Project:** Open Cloud Assistant
**Repository:** `OpenCloudAssistant`
**Maintainer:** Mukesh Krishna Murthy

Open Cloud Assistant is the reusable public project.

Personal installations can use any assistant name they want.

---

## License

Original Open Cloud Assistant integration, deployment, and documentation work
is released under the MIT License.

Third-party projects remain governed by their own licenses and copyright
notices.

---

## No Apple device required

Open Cloud Assistant is **not an Apple-only assistant**.

The assistant core runs on Linux. Users choose how they want to talk to it.

Primary public access paths are:

- **Telegram** — recommended cross-platform default.
- **Discord** — DMs or configured server channels.
- **Browser / Open WebUI** — platform-independent web access.
- **CLI** — direct use, setup, diagnostics, and recovery.
- **iMessage / Apple** — optional integration for users who want it.

A non-Apple user must be able to install Open Cloud Assistant, pass
`opencloud doctor`, and use the assistant without configuring any Apple
device or account.

See `docs/CHANNELS.md` for the complete messaging contract.

## v0.1.0 prerelease scope

This is the first public prerelease of Open Cloud Assistant.

- The documented Ubuntu/ARM64 installation path and CLI workflow have been validated.
- Browser/Open WebUI integration is preview functionality in this prerelease.
- Telegram and Discord require the user's own credentials and configuration.
- iMessage is optional and is not required for a normal installation.

### Ubuntu prerequisites

On a fresh Ubuntu installation, install the base operating-system prerequisites first:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git xz-utils unzip python3 python3-venv python3-pip sudo dbus-user-session procps
```

Then install Open Cloud Assistant:

```bash
git clone https://github.com/Mukeshkr-19/OpenCloudAssistant.git
cd OpenCloudAssistant
./setup.sh --install
opencloud doctor
```
