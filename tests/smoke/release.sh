#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant release gate smoke test"

HELP="$(bin/opencloud help)"

[[ "$HELP" == *"opencloud release check"* ]]
[[ "$HELP" == *"opencloud uninstall"* ]]

OPEN_CLOUD_RELEASE_ALLOW_DIRTY=1 \
    scripts/release-check.sh --static

echo "RELEASE_GATE_SMOKE: PASS"
