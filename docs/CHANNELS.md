# Conversation Channels

Open Cloud Assistant is **not Apple-only**. The assistant core runs on Ubuntu and can be installed with CLI only.

Current channel status:

| Channel | Status |
|---|---|
| CLI | ✅ Validated core path |
| Telegram | 🧪 Guided config implemented; public E2E acceptance pending |
| Discord | 🧪 Guided config implemented; public E2E acceptance pending |
| Browser / Open WebUI | ⚠️ Protected local config only; runtime is preview |
| iMessage / Apple | ➕ Optional; requires compatible runtime |
| Advanced Hermes channels | ➕ Delegated to upstream `hermes gateway setup` |

## Channel command

```bash
./bin/opencloud channels configure
```

The wizard displays:

```text
1) Telegram              [recommended]
2) Discord
3) Browser / Open WebUI
4) CLI only
5) iMessage / Apple      [optional]
6) Advanced channels
7) Configure later
```

Multiple values are accepted, for example `1,4` for Telegram + CLI.

Channel selection is stored in:

```text
~/.opencloud/channels.json
```

Channel/provider secrets are stored separately in:

```text
~/.opencloud/config.env
```

Both are runtime files and should be mode `600`.

## CLI

CLI is the safest first-install path because it requires no messaging credential and no Apple device.

Install CLI-only noninteractively:

```bash
OPEN_CLOUD_CHANNELS=cli ./setup.sh --install
```

Start the assistant:

```bash
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"
hermes chat
```

CLI-only does not require the Hermes messaging gateway service. Fleet maintenance timers still run.

## Telegram — recommended cross-platform messaging

Open Cloud Assistant requires **both**:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USERS
```

The allowlist is explicit and numeric.

### Create a bot

Telegram's official documentation says to message `@BotFather` to register a bot and receive its authentication token. The token gives control of the bot and must remain secret.

Official docs:

- https://core.telegram.org/bots
- https://core.telegram.org/bots/api

### Get the allowed numeric user ID

A Telegram bot cannot initiate the conversation; send your bot a message first.

Then use the official Bot API `getUpdates` method. The following keeps the token out of the literal shell command history and prints only sender IDs from message updates:

```bash
read -rsp "Telegram bot token: " TG_TOKEN; echo
curl -sS "https://api.telegram.org/bot${TG_TOKEN}/getUpdates" | python3 -c 'import json,sys; d=json.load(sys.stdin); ids=[]; [ids.append(x.get("message",{}).get("from",{}).get("id")) for x in d.get("result",[])]; print("User IDs:", ", ".join(str(x) for x in sorted(set(i for i in ids if isinstance(i,int)))))'
unset TG_TOKEN
```

If there is no ID, send the bot another message and retry. If you already configured a webhook elsewhere, `getUpdates` cannot be used simultaneously with that webhook.

### Configure Open Cloud Assistant

```bash
./bin/opencloud channels configure
```

Select Telegram. The wizard asks for the token with hidden input and then asks:

```text
Allowed Telegram user IDs, comma separated:
```

Multiple users can be listed as numeric IDs separated by commas.

After adding Telegram to an already-installed CLI-only deployment:

```bash
./bin/opencloud services install
./bin/opencloud channels status
./bin/opencloud services status
./bin/opencloud doctor
```

The service stage now sees Telegram and makes the Hermes gateway required.

## Discord

Discord's official developer flow starts by creating an application in the Developer Portal. The Bot page is where you obtain/reset the bot token. Discord explicitly treats the token as highly sensitive.

Official docs:

- https://docs.discord.com/developers/quick-start/getting-started
- https://docs.discord.com/developers/bots/overview

### Setup flow

1. Create an application in the Discord Developer Portal.
2. Open its **Bot** page and generate/reset the token.
3. Store that token securely.
4. Install the bot/app into the test context you plan to use.
5. Grant only the permissions your messaging use case needs.
6. Run `./bin/opencloud channels configure` and select Discord.
7. Paste the token at the hidden prompt.
8. Re-run `./bin/opencloud services install`.
9. Check `./bin/opencloud services logs` if the gateway does not connect.

The public channel wizard currently validates that a Discord credential is present; real end-to-end Discord acceptance is still required before stable v1.0.

## Browser / Open WebUI — preview

Selecting Browser generates/protects local API configuration:

```text
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=<generated secret>
```

If an unsafe/nonlocal host is present, the wizard resets it to localhost.

**current release does not claim a release-validated browser runtime.** The service doctor intentionally fails Browser when it is selected so a local config file cannot be mistaken for a working public web deployment.

Do not expose port 8642 directly to the internet. When experimenting, use an authenticated/private tunnel and understand the upstream Hermes API security model.

If you want a clean current doctor result, do not select Browser yet.

## iMessage / Apple — optional

Selecting iMessage asks optionally for Photon project configuration. The channel doctor requires a compatible Photon runtime when iMessage is selected.

No Apple device, Apple ID, Photon project, or iMessage credential is required for CLI, Telegram, or Discord installations.

OpenCloud configures the upstream BlueBubbles/iMessage display as final-only.
Tool progress, reasoning, streaming, interim assistant messages, long-running
notices, iteration detail, and thinking progress stay out of the conversation.
This does not remove CLI/operator diagnostics. The upstream adapter splits long
final responses into ordered messages.

## Advanced channels

Selecting Advanced delegates configuration to upstream:

```bash
hermes gateway setup
```

This keeps Open Cloud Assistant from duplicating every Hermes messaging integration.

## Change channels after installation

```bash
./bin/opencloud channels configure
./bin/opencloud channels status
```

Because messaging selections change whether the Hermes gateway is required, apply the service plan again:

```bash
./bin/opencloud services plan
./bin/opencloud services install
./bin/opencloud doctor
```

## Doctor semantics

`./bin/opencloud doctor` intentionally distinguishes:

- `PASS` — selected channel has its required local configuration/runtime;
- `FAIL` — selected channel is incomplete/broken;
- `SKIP` — optional channel was not selected.

A `SKIP` is not a failure.


## Release acceptance requirements

The stable core release promise is the Linux assistant and CLI operator path.

Optional messaging adapters are interfaces, not separate brains and are not
automatically release blockers simply because support code exists.

For release documentation:

- CLI must remain supported and validated.
- iMessage remains an important optional personal-deployment channel.
- At least one non-Apple adapter may be documented as the recommended
  cross-platform option.
- Telegram, Discord, browser, and other adapters must state their real
  validation status rather than implying end-to-end validation.
- Browser support remains preview until its runtime path is separately
  validated.
- A broken selected/configured adapter must fail doctor; an unselected
  optional adapter must remain SKIP.
