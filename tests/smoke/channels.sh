#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant channel wizard smoke test"

install/90-channels.sh --check

TMP="$(mktemp -d)"

BIN="$TMP/bin"
mkdir -p "$BIN"

printf "%s\n"     "#!/usr/bin/env bash"     "exit 0"     > "$BIN/hermes"

chmod 755 "$BIN/hermes"
export PATH="$BIN:$PATH"

cleanup() {
    rm -rf "$TMP"
}

trap cleanup EXIT

CONFIG="$TMP/config.env"
STATE="$TMP/channels.json"

touch "$CONFIG"
chmod 600 "$CONFIG"

echo "SMOKE: CLI-only"

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py set cli

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py doctor > "$TMP/doctor-cli.txt"

grep -qF "PASS  CLI channel" "$TMP/doctor-cli.txt"

echo "SMOKE: Telegram + Discord + CLI"

TOKEN="123456789:$(printf "A%.0s" {1..32})"

printf "%s\n" \
    "TELEGRAM_BOT_TOKEN=$TOKEN" \
    "TELEGRAM_ALLOWED_USERS=123456789" \
    "DISCORD_BOT_TOKEN=test-discord-token" \
> "$CONFIG"

chmod 600 "$CONFIG"

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py set telegram,discord,cli

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py doctor > "$TMP/doctor-chat.txt"

grep -qF "PASS  Telegram" "$TMP/doctor-chat.txt"
grep -qF "PASS  Discord" "$TMP/doctor-chat.txt"
grep -qF "PASS  CLI channel" "$TMP/doctor-chat.txt"

echo "SMOKE: Browser secure local config"

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py set browser,cli

OPEN_CLOUD_CONFIG="$CONFIG" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py doctor > "$TMP/doctor-browser.txt"

grep -qF "PASS  Browser API" "$TMP/doctor-browser.txt"
grep -qF "API_SERVER_HOST=127.0.0.1" "$CONFIG"
grep -qF "API_SERVER_ENABLED=true" "$CONFIG"

echo "SMOKE: file permissions"

test "$(stat -c %a "$CONFIG")" = "600"
test "$(stat -c %a "$STATE")" = "600"

echo "SMOKE: selected-but-incomplete must FAIL"

EMPTY="$TMP/empty.env"
touch "$EMPTY"
chmod 600 "$EMPTY"

OPEN_CLOUD_CONFIG="$EMPTY" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py set telegram

set +e
OPEN_CLOUD_CONFIG="$EMPTY" OPEN_CLOUD_CHANNELS_STATE="$STATE" \
    python3 scripts/channels.py doctor > "$TMP/doctor-fail.txt"
RC=$?
set -e

test "$RC" -ne 0
grep -qF "FAIL  Telegram" "$TMP/doctor-fail.txt"

echo "SMOKE: command surface"

HELP="$(bin/opencloud help)"
[[ "$HELP" == *"opencloud channels configure"* ]]
[[ "$HELP" == *"opencloud channels status"* ]]

echo "CHANNEL_WIZARD_SMOKE: PASS"
