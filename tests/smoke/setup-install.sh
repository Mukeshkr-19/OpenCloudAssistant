#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant complete installer smoke test"

HELP="$(./setup.sh --help)"
[[ "$HELP" == *"./setup.sh --install"* ]]

if [ "$(uname -s)" != "Linux" ]; then
    echo "SETUP_INSTALL_SMOKE: SKIP (Ubuntu installer preflight required)"
    exit 0
fi

OUT="$(OPEN_CLOUD_SETUP_TEST_MODE=1 ./setup.sh --install 2>&1)"

if [[ "$OUT" == *"HERMES_BRAIN_MATERIALIZATION: PASS"* ]]; then
    [[ "$OUT" == *"HERMES_LIVE_INSTALL_CHECK: PASS"* ]]
    [[ "$OUT" == *"HERMES_ORCHESTRATION_INSTALL_CHECK: PASS"* ]]
    else
    [[ "$OUT" == *"HERMES_COMPATIBILITY_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"* ]]
    [[ "$OUT" == *"HERMES_LIVE_INTEGRATION_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"* ]]
    [[ "$OUT" == *"HERMES_ORCHESTRATION_SOURCE_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"* ]]
fi
[[ "$OUT" == *"VELLUM_CONTEXT_MATERIALIZATION: PASS"* ]]
[[ "$OUT" == *"WORKER_POLICY: PASS"* ]]
[[ "$OUT" == *"SELF_REPAIR_MODE: STAGING_VALIDATE_BACKUP_ROLLBACK"* ]]
[[ "$OUT" == *"FLEET_RUNTIME_INSTALL_CHECK: PASS"* ]]
[[ "$OUT" == *"FLEET_REGISTRY_INSTALL_CHECK: PASS"* ]]
[[ "$OUT" == *"VELLUM_BRIDGE_INSTALL_CHECK: PASS"* ]]
[[ "$OUT" == *"CHANNEL_INSTALL_CHECK: PASS"* ]]
[[ "$OUT" == *"SERVICE_INSTALL_CHECK: PASS"* ]]
[[ "$OUT" == *"SETUP_INSTALL_TEST: PASS"* ]]

echo "SETUP_INSTALL_SMOKE: PASS"
