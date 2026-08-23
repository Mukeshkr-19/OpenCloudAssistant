#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant service installer smoke test"

test -x scripts/runtime-update.sh
test -x scripts/self-heal.sh

TMP_HOME="$(mktemp -d)"
TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP_HOME" "$TMP"
}

trap cleanup EXIT

HOME="$TMP_HOME" OPEN_CLOUD_HOME="$TMP_HOME" install/95-services.sh --check

UPDATER="$TMP_HOME/.local/bin/opencloud-runtime-update"
HEALER="$TMP_HOME/.local/bin/opencloud-self-heal"
test -x "$UPDATER"
test -x "$HEALER"
cmp -s scripts/runtime-update.sh "$UPDATER"
cmp -s scripts/self-heal.sh "$HEALER"

grep -qF 'ExecStart=%h/.local/bin/opencloud-runtime-update --run' \
    services/systemd/opencloud-runtime-update.service
grep -qF 'ExecStart=%h/.local/bin/opencloud-self-heal' \
    services/systemd/opencloud-self-heal.service

HOME="$TMP_HOME" OPEN_CLOUD_HOME="$TMP_HOME" install/95-services.sh --check
cmp -s scripts/runtime-update.sh "$UPDATER"

STATE="$TMP/channels.json"

echo "SMOKE: CLI-only plan"

printf "%s\n" \
    "{\"version\":1,\"selected\":[\"cli\"],\"deferred\":false}" \
    > "$STATE"

PLAN="$(OPEN_CLOUD_CHANNELS_STATE="$STATE" install/95-services.sh --plan)"

[[ "$PLAN" == *"Fleet registry timer: REQUIRED"* ]]
[[ "$PLAN" == *"Fleet verifier timer: REQUIRED"* ]]
[[ "$PLAN" == *"Guarded runtime update timer: REQUIRED"* ]]
[[ "$PLAN" == *"Guarded self-heal timer: REQUIRED"* ]]
[[ "$PLAN" == *"Hermes gateway: SKIP"* ]]

OPEN_CLOUD_ROOT="$ROOT" scripts/runtime-update.sh --check

echo "SMOKE: Telegram plan"

printf "%s\n" \
    "{\"version\":1,\"selected\":[\"telegram\",\"cli\"],\"deferred\":false}" \
    > "$STATE"

PLAN="$(OPEN_CLOUD_CHANNELS_STATE="$STATE" install/95-services.sh --plan)"

[[ "$PLAN" == *"Hermes gateway: REQUIRED"* ]]

echo "SMOKE: Browser release gate"

printf "%s\n" \
    "{\"version\":1,\"selected\":[\"browser\",\"cli\"],\"deferred\":false}" \
    > "$STATE"

PLAN="$(OPEN_CLOUD_CHANNELS_STATE="$STATE" install/95-services.sh --plan)"

[[ "$PLAN" == *"Browser runtime: RELEASE VALIDATION STILL REQUIRED"* ]]

echo "SMOKE: command surface"

HELP="$(bin/opencloud help)"
[[ "$HELP" == *"opencloud services status"* ]]
[[ "$HELP" == *"opencloud services plan"* ]]
[[ "$HELP" == *"opencloud services install"* ]]
[[ "$HELP" == *"opencloud self-heal status"* ]]

echo "SERVICE_INSTALL_SMOKE: PASS"
