# Open Cloud Assistant Documentation

Use this page as the documentation map for the public project.

## Start here

- **[Complete Setup Guide](COMPLETE_SETUP_GUIDE.md)** — beginner path from an empty cloud account to a working CLI assistant.
- **[Oracle Cloud Setup](ORACLE_CLOUD_SETUP.md)** — Oracle Cloud Free Tier / Always Free VM setup and SSH.
- **[Ubuntu / VPS Setup](UBUNTU_VPS_SETUP.md)** — use a non-Oracle Ubuntu server.
- **[Providers](PROVIDERS.md)** — configure NVIDIA and OpenRouter, then refresh/verify the dynamic Fleet.
- **[Channels](CHANNELS.md)** — CLI, Telegram, Discord, Browser preview, optional iMessage.
- **[Operations](OPERATIONS.md)** — services, doctor, logs, restarts, upgrades, validation.
- **[Troubleshooting](TROUBLESHOOTING.md)** — common failures and exact recovery checks.

## Understand the system

- **[Architecture](ARCHITECTURE.md)** — the end-to-end design and trust boundaries.
- [Hermes and Vellum](HERMES_VELLUM.md) — deterministic personal-context bridge and read/write contract.
- [Fleet Runtime](FLEET_RUNTIME.md) — dispatcher and provider permission boundaries.
- [Fleet Registry](FLEET_REGISTRY.md) — dynamic discovery and verification state.
- [Fleet Installation](FLEET_INSTALL.md) — runtime paths and Fleet commands.
- [Self-Repair](SELF_REPAIR.md) — staged restricted OpenCode repair.
- [Services](SERVICES.md) — systemd user-service layer.
- [Materialization](MATERIALIZATION.md) — sanitized integration references and portability.
- [Install Pipeline](INSTALL_PIPELINE.md) — the 14-stage install pipeline.
- [Compatibility](COMPATIBILITY.md) — captured validated environment.
- [Roadmap](ROADMAP.md) — completed work and remaining stable-release gates.

## Repository security

- [Security policy](../SECURITY.md)
- [Third-party notices](../THIRD_PARTY_NOTICES.md)
- [Contributing](../CONTRIBUTING.md)

## Command convention

The v0.1.0 repository always contains its command wrapper at:

```bash
./bin/opencloud
```

Documentation uses that form so commands work even when no global `opencloud` symlink is present. If your shell already resolves `opencloud`, either form is fine.
