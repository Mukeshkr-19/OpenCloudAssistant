#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant Hermes live installer smoke test"

install/35-hermes-live.sh --check

TMP="$(mktemp -d)"

cleanup() {
    python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)" "$TMP"
}

trap cleanup EXIT

mkdir -p "$TMP/hermes"

git -C "$HOME/.hermes/hermes-agent" archive HEAD | tar -x -C "$TMP/hermes"

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
test -f "$TMP/hermes/agent/hermes_fleet_bridge.py"

SECOND="$(
    OPEN_CLOUD_HOME="$TMP/home" \
    OPEN_CLOUD_HERMES_ROOT="$TMP/hermes" \
        install/35-hermes-live.sh --install
)"

printf "%s\n" "$SECOND"
printf "%s\n" "$SECOND" | grep -qF "HERMES_LIVE_INSTALL: ALREADY_PRESENT"

echo "HERMES_LIVE_INSTALL_SMOKE: PASS"
