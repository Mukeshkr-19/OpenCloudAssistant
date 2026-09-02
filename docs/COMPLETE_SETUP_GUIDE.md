# Complete Setup Guide

This is the beginner path for Open Cloud Assistant current release. It starts before you have a server and ends with a validated CLI assistant, configured AI providers, optional messaging, and always-on maintenance services.

> **Release scope:** Ubuntu 24.04 on ARM64 is the clean-install path validated for this release. The installer accepts x86_64, but a clean x86_64 end-to-end release proof is still pending. Browser/Open WebUI is preview-only. Telegram and Discord configuration are implemented, but public end-to-end messaging acceptance remains a stable-release gate.

## 1. What you need

You need:

- a computer with a terminal and web browser;
- an Ubuntu 24.04 server with internet access;
- an SSH key for that server;
- at least one usable AI provider account/key;
- optionally, a Telegram or Discord account if you want messaging.

The recommended beginner deployment is an Oracle Cloud `VM.Standard.A1.Flex` Ubuntu instance. The Oracle-specific walkthrough is in [ORACLE_CLOUD_SETUP.md](ORACLE_CLOUD_SETUP.md). If you already have another Ubuntu VPS, use [UBUNTU_VPS_SETUP.md](UBUNTU_VPS_SETUP.md) and continue at section 4.

## 2. Create the cloud server

For Oracle Cloud, use the official OCI console and create an Ubuntu 24.04 ARM64 VM in your **home region**. Oracle documents that Always Free compute must be created in the home region and that current Always Free A1 capacity is equivalent to 2 OCPUs and 12 GB of memory across A1 instances.

Recommended project profile when Always Free A1 capacity is available:

| Setting | Recommended value |
|---|---|
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.A1.Flex` |
| OCPUs | 2 |
| Memory | 12 GB |
| Boot volume | 50 GB or the OCI default above the minimum |
| Network | Public subnet with public IPv4 |
| SSH | Key-based authentication |

Using 2 OCPUs / 12 GB consumes the documented Always Free A1 allowance for an Always Free tenancy. Capacity can be temporarily unavailable. See the Oracle guide for current official references and alternatives.

### Network rule

For a basic SSH-managed deployment, you need SSH access on TCP port 22. Prefer limiting SSH ingress to your own public IP/CIDR when practical.

**Do not open port 8642 or the Vellum runtime directly to the whole internet.** The Browser/API preview is intentionally configured on localhost. Telegram and Discord do not require you to expose the Hermes API port publicly.

## 3. SSH into Ubuntu

OCI Ubuntu images use the `ubuntu` SSH username.

On macOS or Linux:

```bash
chmod 400 ~/Downloads/your-oci-private-key.key
ssh -i ~/Downloads/your-oci-private-key.key ubuntu@YOUR_SERVER_PUBLIC_IP
```

On modern Windows PowerShell with OpenSSH:

```powershell
ssh -i C:\Users\YOUR_NAME\Downloads\your-oci-private-key.key ubuntu@YOUR_SERVER_PUBLIC_IP
```

After login, confirm the machine:

```bash
cat /etc/os-release
uname -m
```

For the release-validated path you should see Ubuntu 24.04 and `aarch64`/ARM64.

## 4. Install Ubuntu prerequisites

Run this **inside the Ubuntu server**:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git xz-utils unzip python3 python3-venv python3-pip sudo dbus-user-session procps
```

The supported installer checks the required Ubuntu base packages. `./setup.sh --install` can bootstrap missing supported packages through `apt-get`, while `./setup.sh --dry-run` reports them as `WOULD_INSTALL` without changing the host. The list below remains useful when preparing the machine manually.

## 5. Clone Open Cloud Assistant

```bash
cd ~
git clone https://github.com/Mukeshkr-19/OpenCloudAssistant.git
cd OpenCloudAssistant
```

Before making changes, run the non-mutating validation path:

```bash
./setup.sh --dry-run
```

A healthy dry run ends with:

```text
SETUP_DRY_RUN: PASS
```

If it does not, stop and use [TROUBLESHOOTING.md](TROUBLESHOOTING.md) instead of continuing blindly.

## 6. Install the core assistant

The easiest first installation is **CLI only**. It avoids messaging credentials until the core is healthy:

```bash
OPEN_CLOUD_CHANNELS=cli ./setup.sh --install
```

The installer runs 14 stages:

1. preflight;
2. Hermes installation;
3. Vellum installation;
4. Hermes compatibility validation;
5. live Hermes integration;
6. context and worker contracts;
7. restricted self-repair;
8. dynamic Fleet runtime;
9. dynamic Fleet registry;
10. Hermes ↔ Vellum bridge;
11. Hermes orchestration;
12. channels;
13. always-on services;
14. final doctor.

A successful install ends with:

```text
SETUP_INSTALL: PASS
```

The installer is designed to be rerunnable. The validated second-run path preserves existing runtime registry/health state and treats already-installed integration stages idempotently.

### Interactive channel setup during install

If you prefer to configure channels during installation, run:

```bash
./setup.sh --install
```

When no channel selection already exists, an interactive terminal displays:

```text
1) Telegram              [recommended]
2) Discord
3) Browser / Open WebUI
4) CLI only
5) iMessage / Apple      [optional]
6) Advanced channels
7) Configure later
```

Multiple selections are allowed, such as `1,4` for Telegram + CLI.

For a first public deployment, CLI-only first is simpler because Browser is still preview and Telegram/Discord E2E acceptance is not yet complete.

## 7. Make the installed CLIs visible in this shell

The setup scripts know where Hermes, Bun, and Vellum are installed, but your current parent shell may not have reloaded its profile yet. Run:

```bash
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"
```

You can add the same line to your shell profile later if your installer did not already do so.

The Open Cloud Assistant wrapper itself is always available from the repository as:

```bash
./bin/opencloud help
```

## 8. Configure AI providers

The assistant needs usable model capacity for real conversations. The recommended free-first pair is:

- **NVIDIA Build/NIM API key** for dynamically discovered NVIDIA models;
- **OpenRouter API key** for the stable `openrouter/free` fallback route.

Run:

```bash
./bin/opencloud providers configure
```

The prompts are hidden; values are not echoed. They are saved to:

```text
~/.opencloud/config.env
```

with restrictive permissions.

Then refresh and verify the dynamic Fleet:

```bash
./bin/opencloud providers status
./bin/opencloud fleet refresh
./bin/opencloud fleet proof
```

A provider may be configured but still have no currently verified free capacity. That is a provider/runtime condition, not a reason to hard-code a temporary model ID into source.

### Manual configuration alternative

If you need to edit the runtime file directly:

```bash
nano ~/.opencloud/config.env
```

Use only the keys you actually have:

```text
NVIDIA_API_KEY=YOUR_KEY
OPENROUTER_API_KEY=YOUR_KEY
```

Then protect the file and refresh:

```bash
chmod 600 ~/.opencloud/config.env
./bin/opencloud fleet refresh
```

Do **not** put real keys into the repository's `.env.example`. That file is a key-name reference only.

See [PROVIDERS.md](PROVIDERS.md) for where to create provider keys and how Fleet routing works.

## 9. Run the doctor

```bash
./bin/opencloud doctor
```

Doctor uses three states:

- `PASS` — a selected/required component is healthy;
- `FAIL` — a selected/required component is broken;
- `SKIP` — an optional component was not selected or is intentionally disabled.

A `SKIP` for Discord or iMessage is normal when you did not select them. A
`Gemini lane` SKIP means the dynamic lane lacks a configured key or fresh
verification; it is not enabled by a hardcoded model ID.

If you selected Browser, the service doctor intentionally reports the Browser runtime release gate as a failure because current release has only the protected localhost configuration, not a release-validated browser service.

## 10. Start your first CLI conversation

```bash
hermes chat
```

Start with a simple prompt such as:

```text
Reply with exactly: OPEN_CLOUD_ASSISTANT_OK
```

Then try a normal multi-step task. Hermes is the only user-facing orchestrator; it decides internally when temporary workers, model routing, tools, or personal context are useful.

Do not expect a fresh Vellum installation to know personal facts you have never stored. Personal memory is runtime state, not bundled with the public repository.

## 11. Add Telegram after the core works

Telegram is the recommended cross-platform messaging option.

### Create the bot

1. Open Telegram and message the official `@BotFather` account.
2. Create a bot and copy the authentication token.
3. Keep the token private. Anyone with the token can control the bot.
4. Send a message to your new bot from the Telegram account you want to allow.

### Get your numeric Telegram user ID using the official Bot API

The Open Cloud Assistant allowlist requires numeric user IDs. After you have sent your bot a message, on the Ubuntu server run:

```bash
read -rsp "Telegram bot token: " TG_TOKEN; echo
curl -sS "https://api.telegram.org/bot${TG_TOKEN}/getUpdates" | python3 -c 'import json,sys; d=json.load(sys.stdin); ids=[]; [ids.append(x.get("message",{}).get("from",{}).get("id")) for x in d.get("result",[])]; print("User IDs:", ", ".join(str(x) for x in sorted(set(i for i in ids if isinstance(i,int)))))'
unset TG_TOKEN
```

This prints IDs rather than dumping your full message update payload. If it prints no ID, send the bot another normal message and retry.

### Configure the channel

```bash
./bin/opencloud channels configure
```

Select Telegram (and optionally CLI). The wizard asks for:

- Telegram bot token;
- allowed numeric Telegram user IDs, comma-separated.

Then re-run the service installer because adding a messaging channel changes whether the Hermes gateway is required:

```bash
./bin/opencloud services install
./bin/opencloud channels status
./bin/opencloud services status
./bin/opencloud doctor
```

Finally, message the bot from an allowed account.

## 12. Add Discord

1. Create an application in the Discord Developer Portal.
2. Use its Bot page to generate/reset a bot token.
3. Store the token securely; Discord treats it as a sensitive credential.
4. Install the app/bot where you plan to test it and grant only the permissions it actually needs.
5. Run `./bin/opencloud channels configure` and select Discord.
6. Run `./bin/opencloud services install` again.
7. Check `./bin/opencloud services logs` if the bot does not connect.

See [CHANNELS.md](CHANNELS.md) for current release acceptance status and troubleshooting details.

## 13. Keep it running after logout/reboot

Open Cloud Assistant installs systemd **user** timers for Fleet registry refresh/verification. Messaging selections that need Hermes Gateway also enable the gateway service.

Check:

```bash
./bin/opencloud services status
```

For user services to survive without an interactive login, linger must be enabled. The installer attempts this when it can. If doctor says it is missing:

```bash
sudo loginctl enable-linger "$USER"
```

Then verify again:

```bash
./bin/opencloud doctor
```

## 14. Useful daily commands

```bash
./bin/opencloud doctor
./bin/opencloud providers status
./bin/opencloud fleet status
./bin/opencloud fleet proof
./bin/opencloud channels status
./bin/opencloud services status
./bin/opencloud services logs
```

After changing provider credentials:

```bash
./bin/opencloud fleet refresh
```

After changing Telegram/Discord/iMessage/advanced channel selection:

```bash
./bin/opencloud services install
```

To restart the gateway deliberately:

```bash
./bin/opencloud services restart-gateway
```

Routine gateway restart/shutdown notifications are suppressed from normal user conversations by default; internal interruption/recovery behavior and service logs remain active.

## 15. Security checklist before you leave it online

- SSH uses a private key, not a shared password.
- Port 22 is restricted to your IP/CIDR when practical.
- Port 8642 is **not** exposed to the whole internet.
- `~/.opencloud/config.env` is mode `600`.
- Telegram has an explicit numeric user allowlist.
- Bot/API tokens are not in Git, screenshots, shell transcripts, or public issues.
- Browser preview remains bound to localhost.
- You understand that external AI providers receive the prompts routed to them; free-provider privacy/retention policies vary by provider.

Run the repository audit if you change public source or docs:

```bash
./scripts/public-audit.sh
```

## 16. Validate the repository itself

For contributors or before a release update:

```bash
./scripts/public-audit.sh
./tests/smoke/run.sh
```

Do not use `./setup.sh --test`; that is not a supported public command. Use `./setup.sh --dry-run` for the non-mutating setup check.

## 17. What is not finished in current release

The release is intentionally transparent about its remaining gates:

- real public Telegram end-to-end acceptance;
- real public Discord end-to-end acceptance;
- Browser/Open WebUI end-to-end runtime;
- clean Ubuntu x86_64 reproducibility proof;
- automated Ubuntu prerequisite bootstrap;
- supported uninstall and full backup/restore workflow;
- stable v1.0 release acceptance.

See [ROADMAP.md](ROADMAP.md) for the maintained checklist.
