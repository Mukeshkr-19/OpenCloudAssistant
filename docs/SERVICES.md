# Always-On Services

Open Cloud Assistant uses systemd user services on Linux.

## Dynamic Fleet

The service layer manages:

    hermes-fleet-registry.service
    hermes-fleet-registry.timer
    hermes-fleet-verifier.service
    hermes-fleet-verifier.timer

Registry discovery and compatibility verification run periodically.

Provider credentials are loaded at runtime from:

    ~/.opencloud/config.env

Secrets are not stored in the systemd unit files.

## Hermes gateway

Open Cloud Assistant does not fork Hermes gateway service internals.

When Telegram, Discord, iMessage, or advanced messaging is selected,
service installation delegates gateway creation to:

    hermes gateway install

Open Cloud Assistant adds a systemd drop-in to load the local Open Cloud
Assistant configuration environment.

CLI-only installations do not require the messaging gateway.

## Boot persistence

User-level services require lingering for reliable boot-time execution without
an interactive login session.

When possible the installer enables this automatically.

Otherwise run:

    sudo loginctl enable-linger $USER

## Browser

Browser selection currently prepares protected localhost configuration.

End-to-end browser runtime remains a release-validation requirement.

## Commands

    opencloud services plan
    opencloud services status
    opencloud services install
    opencloud services restart-gateway
    opencloud services logs
