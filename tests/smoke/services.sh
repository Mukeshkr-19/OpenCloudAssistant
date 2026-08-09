#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant service installer smoke test"

install/95-services.sh --check

TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}

trap cleanup EXIT

STATE="$TMP/channels.json"

echo "SMOKE: CLI-only plan"

printf "%s\n" \
    "{\"version\":1,\"selected\":[\"cli\"],\"deferred\":false}" \
    > "$STATE"

PLAN="$(OPEN_CLOUD_CHANNELS_STATE="$STATE" install/95-services.sh --plan)"

[[ "$PLAN" == *"Fleet registry timer: REQUIRED"* ]]
[[ "$PLAN" == *"Fleet verifier timer: REQUIRED"* ]]
[[ "$PLAN" == *"Hermes gateway: SKIP"* ]]

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

echo "SERVICE_INSTALL_SMOKE: PASS"
