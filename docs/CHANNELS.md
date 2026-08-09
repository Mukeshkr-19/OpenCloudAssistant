# Talking to Open Cloud Assistant

Apple hardware is **not required** to install or use Open Cloud Assistant.

The core assistant runs on the Linux host. Messaging is a separate interface
that users choose after the core system is operational.

A user may configure one channel, several channels, CLI only, or configure
messaging later.

## Recommended channel order

### 1. Telegram — recommended default

Telegram is the recommended first option for users who want the simplest
cross-platform phone and desktop experience.

It works without a Mac or iPhone.

Target setup flow:

1. Create a Telegram bot through BotFather.
2. Keep the bot token private.
3. Obtain the numeric Telegram user ID that should be allowed.
4. Run the Hermes gateway setup flow.
5. Configure the Telegram bot.
6. Restrict the bot to explicit allowed users.
7. Start or restart the Hermes gateway.
8. Send a normal test message.

The public installer should guide the user through these steps rather than
assuming they already know Hermes configuration.

## 2. Discord

Discord is another primary cross-platform option.

Target setup flow:

1. Create a Discord bot/application.
2. Keep the bot token private.
3. Configure the bot through the Hermes gateway.
4. Start with a direct-message test.
5. Configure server/channel access only when needed.

Discord setup failure must not make the core assistant unhealthy when the
user did not select Discord.

## 3. Browser / Open WebUI

A browser interface gives users a platform-independent option from Windows,
Linux, macOS, ChromeOS, Android, iOS, or another machine with a modern browser.

The planned browser path will connect a protected web frontend to the Hermes
API interface.

The installer must never expose an unauthenticated assistant API directly to
the public internet.

## 4. CLI

CLI access is the universal fallback and diagnostic interface.

A successful core installation should be usable from the command line before
the installer claims that optional messaging setup is complete.

## 5. iMessage / Apple integration

iMessage is **optional**.

Users with compatible Apple hardware may configure the supported Apple
messaging path.

Users without Apple hardware must never be required to provide:

- a Mac,
- an iPhone,
- an Apple ID,
- iMessage credentials,
- or Apple-specific configuration.

The installer should ask Apple-specific questions only when the user
explicitly selects the iMessage option.

## Installer channel menu

The target setup flow is:

    How do you want to talk to your assistant?

      1) Telegram              [recommended]
      2) Discord
      3) Browser / Open WebUI
      4) CLI only
      5) iMessage / Apple      [optional]
      6) Advanced channels
      7) Configure later

Users may enable more than one channel.

Core installation must remain independent from optional messaging setup.

## Doctor behavior

`opencloud doctor` must distinguish three states:

- PASS — selected/configured component works.
- FAIL — selected/required component is broken.
- SKIP — optional component was not configured.

Example:

    Core assistant        PASS
    Hermes               PASS
    Vellum               PASS
    Telegram             PASS
    Discord              SKIP
    Browser UI           SKIP
    iMessage             SKIP

A SKIP is not a failure.

Someone using Android plus Windows should be able to run Telegram, Discord,
or the browser interface and receive a completely healthy doctor result.

## Release acceptance requirements

Before a stable public release:

- non-Apple installation must work end-to-end;
- Telegram must be tested end-to-end;
- Discord must be tested end-to-end;
- browser access must be tested end-to-end;
- CLI access must be tested;
- iMessage must remain optional;
- Apple questions must appear only when iMessage is selected;
- doctor must distinguish PASS, FAIL, and SKIP;
- every supported primary channel must include a real test-message step.
