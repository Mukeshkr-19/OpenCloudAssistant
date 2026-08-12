#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant dynamic Fleet registry smoke test"

test -f integrations/fleet/registry/refresh.py
test -f integrations/fleet/registry/verify.py
test -x install/75-fleet-registry.sh
test -x scripts/providers.sh
test -x scripts/fleet-refresh.sh

PYTHON="$(command -v python3)"

"$PYTHON" -m py_compile integrations/fleet/registry/refresh.py integrations/fleet/registry/verify.py
rm -rf integrations/fleet/registry/__pycache__

echo "SMOKE: source compile PASS"

install/75-fleet-registry.sh --check

echo "SMOKE: installer check PASS"

TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}

trap cleanup EXIT

OPEN_CLOUD_FLEET_HOME="$TMP/fleet" install/75-fleet-registry.sh --install

test -f "$TMP/fleet/registry/refresh.py"
test -f "$TMP/fleet/registry/verify.py"
test -f "$TMP/fleet/fleet_runtime.py"

echo "SMOKE: isolated install PASS"

OPEN_CLOUD_FLEET_HOME="$TMP/fleet" OPEN_CLOUD_HERMES_PYTHON="$PYTHON" scripts/fleet-refresh.sh check

echo "SMOKE: refresh wrapper PASS"

CONFIG="$TMP/config.env"
touch "$CONFIG"
chmod 600 "$CONFIG"

OPEN_CLOUD_CONFIG="$CONFIG" scripts/providers.sh status

MODE="$(stat -c %a "$CONFIG" 2>/dev/null || stat -f %Lp "$CONFIG")"
test "$MODE" = "600"

echo "SMOKE: provider config PASS"

HELP_OUTPUT="$(bin/opencloud help)"
[[ "$HELP_OUTPUT" == *"opencloud fleet refresh"* ]]
[[ "$HELP_OUTPUT" == *"opencloud fleet verify"* ]]
[[ "$HELP_OUTPUT" == *"opencloud fleet proof"* ]]
[[ "$HELP_OUTPUT" == *"opencloud providers configure"* ]]

grep -qF "openrouter/free" config/fleet/hermes-fleet-policy.json

echo "FLEET_REGISTRY_SMOKE: PASS"
