# Cross-Platform Channel Wizard

Open Cloud Assistant does not require Apple hardware.

The managed channel command is:

    opencloud channels configure

The wizard supports:

- Telegram as the recommended cross-platform messaging option.
- Discord.
- Browser/API local configuration.
- CLI-only operation.
- Optional iMessage/Apple integration.
- Advanced upstream Hermes channels.
- Configuring messaging later.

Multiple primary channels may be selected.

## Telegram

Telegram requires both TELEGRAM_BOT_TOKEN and an explicit
TELEGRAM_ALLOWED_USERS allowlist.

## Discord

Discord requires DISCORD_BOT_TOKEN.

Advanced Discord settings remain available through Hermes.

## Browser

Selecting Browser prepares a protected API configuration bound to localhost.

The wizard generates API_SERVER_KEY when needed and does not configure
0.0.0.0 exposure.

The actual browser service is validated during the service stage.

## CLI

CLI-only operation requires no messaging credentials and no Apple hardware.

## iMessage

iMessage is optional.

Apple or Photon configuration is requested only when iMessage is selected.

## Advanced channels

Advanced configuration delegates to:

    hermes gateway setup

## State

Channel selection is stored locally in:

    ~/.opencloud/channels.json

Secrets remain in:

    ~/.opencloud/config.env

Both runtime files use mode 600.

## Doctor semantics

Unselected optional channels are SKIP.

A selected channel with missing required configuration is FAIL.

A selected channel whose required local configuration is present is PASS.

End-to-end messaging connectivity remains a release-validation requirement.
