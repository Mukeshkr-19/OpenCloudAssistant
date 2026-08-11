# Ubuntu / VPS Setup

You do not need Oracle Cloud to run Open Cloud Assistant. The public server reference targets Ubuntu 24.04 and the preflight accepts ARM64 (`aarch64`/`arm64`) and x86_64 (`x86_64`/`amd64`).

> The clean release proof for current release is Ubuntu 24.04 ARM64. x86_64 is accepted by the installer checks but still needs a clean end-to-end release proof before stable v1.0.

## Recommended host characteristics

- Ubuntu 24.04 LTS;
- persistent home directory;
- outbound HTTPS access;
- systemd user services;
- Python 3;
- enough memory for Hermes/Vellum plus the browser dependencies installed by upstream tooling.

The known ARM64 release validation used a 2 OCPU / 12 GB class host. Smaller hosts may work but are not the current release release reference.

## SSH/networking

Use SSH key authentication. For a public VPS, restrict TCP 22 to trusted source networks where practical.

Do not expose the Hermes API (`8642`) to `0.0.0.0` just to make setup easier. Browser preview configuration is localhost-only by design.

## Install prerequisites

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git xz-utils unzip python3 python3-venv python3-pip sudo dbus-user-session procps
```

## Clone and validate

```bash
cd ~
git clone https://github.com/Mukeshkr-19/OpenCloudAssistant.git
cd OpenCloudAssistant
./setup.sh --dry-run
```

## Install CLI-first

```bash
OPEN_CLOUD_CHANNELS=cli ./setup.sh --install
```

Then configure providers:

```bash
./bin/opencloud providers configure
./bin/opencloud fleet refresh
./bin/opencloud doctor
```

See [COMPLETE_SETUP_GUIDE.md](COMPLETE_SETUP_GUIDE.md) for the remaining provider, conversation, messaging, and operations steps.
