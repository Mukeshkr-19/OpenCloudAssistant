# Troubleshooting

Start every diagnosis from the repository:

```bash
cd ~/OpenCloudAssistant
./bin/opencloud doctor
```

Then use the smallest relevant check below.

## `opencloud: command not found`

current release guarantees the wrapper inside the repository, not a global shell command.

Use:

```bash
./bin/opencloud doctor
```

If Hermes/Bun/Vellum commands are missing from the current shell after installation:

```bash
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"
```

## `./setup.sh --test` fails / unknown option

That is not a supported public command.

Use the non-mutating validation mode:

```bash
./setup.sh --dry-run
```

Install mode is:

```bash
./setup.sh --install
```

## Preflight says a dependency is missing

Install the clean-Ubuntu prerequisites:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git xz-utils unzip python3 python3-venv python3-pip sudo dbus-user-session procps
```

Then retry:

```bash
./setup.sh --dry-run
```

## Oracle says `Out of host capacity`

This is an OCI capacity issue, not an Open Cloud Assistant code failure. Oracle's Always Free documentation recommends trying another availability domain where available or retrying later.

Do not permanently redesign the project or choose a paid resource without understanding the billing impact just because A1 is temporarily unavailable.

## SSH: `Permission denied (publickey)`

Check all four common causes:

1. username is `ubuntu` for an Ubuntu OCI image;
2. you are using the private key that matches the public key installed on the instance;
3. the public IP is correct;
4. local private-key permissions are restrictive.

macOS/Linux:

```bash
chmod 400 ~/Downloads/your-private-key.key
ssh -i ~/Downloads/your-private-key.key ubuntu@YOUR_PUBLIC_IP
```

Also confirm OCI/network security allows inbound TCP 22 from your current source IP.

## Doctor: NVIDIA configured but no verified capacity

Run:

```bash
./bin/opencloud providers status
./bin/opencloud fleet refresh
./bin/opencloud fleet proof
```

If the key is valid but zero models verify, the provider may currently have no compatible/available capacity for your account. The public design intentionally does not solve this by pinning a random temporary model ID.

## Doctor: OpenRouter configured but unhealthy

Run:

```bash
./bin/opencloud fleet refresh
./bin/opencloud fleet proof
```

Confirm your OpenRouter key is current. The permanent fallback route is `openrouter/free`; free availability and rate limits are controlled externally.

## Doctor: Zen is `SKIP`

This is usually normal. Zen is optional and only becomes eligible when the OpenCode client plus verified free capacity/account access are available.

The core assistant should not fail merely because Zen is unavailable.

## Doctor: Gemini is `SKIP`

Expected current behavior:

```text
SKIP  Gemini lane  dynamic registry lane; requires configured key and fresh verification
```

Do not remove the guard just to make every line say PASS.

## Doctor fails because Browser is selected

Expected current behavior. The Browser wizard prepares protected localhost API configuration, but the service layer intentionally marks the Browser runtime as an uncompleted release gate.

For the supported current release path, reconfigure without Browser:

```bash
./bin/opencloud channels configure
./bin/opencloud services install
./bin/opencloud doctor
```

Do not "fix" this by exposing `0.0.0.0:8642` to the internet.

## Telegram is selected but doctor says incomplete

Telegram requires both a token and an explicit numeric allowlist.

Re-run:

```bash
./bin/opencloud channels configure
```

Check without printing secrets:

```bash
./bin/opencloud channels status
```

Then:

```bash
./bin/opencloud services install
./bin/opencloud doctor
```

## Telegram is configured but does not reply

Check the gateway:

```bash
./bin/opencloud services status
./bin/opencloud services logs
```

Also confirm:

- you sent a message to the bot first;
- the sending Telegram account's numeric ID is in `TELEGRAM_ALLOWED_USERS`;
- you did not accidentally use a bot username instead of a numeric user ID;
- the bot token has not been rotated/revoked.

Telegram E2E remains a public acceptance gate for stable v1.0, so preserve useful sanitized error details when opening an issue.

## Discord is configured but does not reply

Check:

```bash
./bin/opencloud channels status
./bin/opencloud services status
./bin/opencloud services logs
```

Then verify in the Discord Developer Portal that:

- the token is current;
- the app/bot is installed in the context you are testing;
- required bot permissions/intents for your chosen messaging behavior are enabled.

Do not paste the token into an issue.

## Hermes gateway is inactive

If a messaging channel is selected:

```bash
./bin/opencloud services plan
./bin/opencloud services install
./bin/opencloud services status
```

Then inspect:

```bash
./bin/opencloud services logs
```

For CLI-only, gateway `SKIP` is normal.

## User services stop after logout/reboot

Check linger:

```bash
loginctl show-user "$USER" -p Linger
```

Enable:

```bash
sudo loginctl enable-linger "$USER"
```

Then:

```bash
systemctl --user daemon-reload
./bin/opencloud services install
./bin/opencloud doctor
```

## Provider keys are in the wrong file

Managed runtime secrets belong in:

```text
~/.opencloud/config.env
```

The repository `.env.example` is a reference and must not contain real secrets.

Recommended recovery:

```bash
./bin/opencloud providers configure
chmod 600 ~/.opencloud/config.env
./bin/opencloud fleet refresh
```

## Config permission failure

Check:

```bash
stat -c '%a %n' ~/.opencloud/config.env
```

Fix:

```bash
chmod 600 ~/.opencloud/config.env
```

## I restarted the gateway and did not receive a shutdown warning

That is expected for this deployment. Routine gateway lifecycle messages are suppressed from normal user conversations by default while the internal interruption/recovery state and logs remain active.

If you intentionally want the upstream notices, set:

```text
HERMES_GATEWAY_LIFECYCLE_NOTICES=1
```

in the service environment and restart the gateway.

## A Hermes upstream update breaks the live integration

Do not manually force patches into an unknown upstream source tree.

Run:

```bash
./setup.sh --dry-run
```

The compatibility/materialization checks should fail rather than silently applying an incompatible change. Keep the failing output, upstream Hermes version/commit, and the Open Cloud Assistant release version when reporting the issue.

## Repository validation fails

Run separately:

```bash
./scripts/public-audit.sh
./tests/smoke/run.sh
```

If `public-audit` fails, inspect exactly what file/value shape triggered it before committing. Never weaken the audit just to make a secret-shaped artifact pass.

## Last-resort information to collect safely

Useful non-secret diagnostics:

```bash
./bin/opencloud doctor
./bin/opencloud providers status
./bin/opencloud fleet proof
./bin/opencloud channels status
./bin/opencloud services status
```

Before sharing logs, remove personal prompts, tokens, IPs you do not want public, auth artifacts, and private memory/context.
