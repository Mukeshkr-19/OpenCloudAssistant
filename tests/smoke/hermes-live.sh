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

SECOND="$(
    OPEN_CLOUD_HOME="$TMP/home" \
    OPEN_CLOUD_HERMES_ROOT="$TMP/hermes" \
        install/35-hermes-live.sh --install
)"

printf "%s\n" "$SECOND"
printf "%s\n" "$SECOND" | grep -qF "HERMES_LIVE_INSTALL: ALREADY_PRESENT"

echo "HERMES_LIVE_INSTALL_SMOKE: PASS"
