#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

HERMES_SOURCE="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

echo "Open Cloud Assistant Hermes live installer smoke test"

grep -qF 'silent_gateway_lifecycle.py" "$out/gateway/run.py"' install/35-hermes-live.sh

tree_hash() {
    (cd "$1" && find . -path './.git' -prune -o -type f -print0 | sort -z | xargs -0 shasum -a 256) | shasum -a 256
}
BEFORE="$(tree_hash "$HERMES_SOURCE")"
OPEN_CLOUD_HERMES_ROOT="$HERMES_SOURCE" install/35-hermes-live.sh --check
AFTER="$(tree_hash "$HERMES_SOURCE")"
[ "$BEFORE" = "$AFTER" ]
echo "PASS check preserves selected tree while validating disposable gateway patch"

TMP="$(mktemp -d)"

cleanup() {
    python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)" "$TMP"
}

trap cleanup EXIT

mkdir -p "$TMP/hermes"

git -C "$HERMES_SOURCE" archive HEAD | tar -x -C "$TMP/hermes"

git -C "$TMP/hermes" init -q
git -C "$TMP/hermes" config maintenance.auto false
git -C "$TMP/hermes" add .

git -C "$TMP/hermes" \
    -c user.name=OpenCloudTest \
    -c user.email=test@example.invalid \
    -c maintenance.auto=false \
    commit -qm baseline

FIRST="$(
    OPEN_CLOUD_HOME="$TMP/home" \
    OPEN_CLOUD_HERMES_ROOT="$TMP/hermes" \
        install/35-hermes-live.sh --install
)"

printf "%s\n" "$FIRST"
printf "%s\n" "$FIRST" | grep -qF "HERMES_LIVE_INSTALL: PASS"

grep -RqsF "HERMES_FLEET_MAIN_ATTACH_BEGIN" "$TMP/hermes/agent" "$TMP/hermes/tools"
grep -RqsF "HERMES_FLEET_WORKER_ATTACH_BEGIN" "$TMP/hermes/agent" "$TMP/hermes/tools"
grep -RqsF "HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1" "$TMP/hermes/agent" "$TMP/hermes/tools"
grep -qF "HERMES_SILENT_GATEWAY_LIFECYCLE_NOTICE_V1" "$TMP/hermes/gateway/run.py"
test -f "$TMP/hermes/agent/hermes_fleet_bridge.py"

cp "$TMP/hermes/gateway/run.py" "$TMP/expected-gateway.py"

# Simulate an older OpenCloud-managed Hermes tree:
# historical integration markers remain present, but a managed
# file differs from the newly materialized desired tree.
printf '\n# OPEN_CLOUD_STALE_INSTALL_FIXTURE\n' >> \
    "$TMP/hermes/gateway/run.py"

SECOND="$(
    OPEN_CLOUD_HOME="$TMP/home" \
    OPEN_CLOUD_HERMES_ROOT="$TMP/hermes" \
        install/35-hermes-live.sh --install
)"

printf "%s\n" "$SECOND"

printf "%s\n" "$SECOND" |
    grep -qF "HERMES_LIVE_INSTALL: PASS"

printf "%s\n" "$SECOND" |
    grep -qF "HERMES_LIVE_BACKUP:"

cmp -s \
    "$TMP/expected-gateway.py" \
    "$TMP/hermes/gateway/run.py"

! grep -qF \
    "OPEN_CLOUD_STALE_INSTALL_FIXTURE" \
    "$TMP/hermes/gateway/run.py"

echo "PASS stale marker-present install is upgraded"

THIRD="$(
    OPEN_CLOUD_HOME="$TMP/home" \
    OPEN_CLOUD_HERMES_ROOT="$TMP/hermes" \
        install/35-hermes-live.sh --install
)"

printf "%s\n" "$THIRD"

printf "%s\n" "$THIRD" |
    grep -qF "HERMES_LIVE_INSTALL: ALREADY_PRESENT"

echo "PASS exact desired install remains idempotent"
echo "HERMES_LIVE_INSTALL_SMOKE: PASS"
