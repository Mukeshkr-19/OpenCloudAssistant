#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant reliability tests"

test -f integrations/fleet/dispatcher.py
test -f config/fleet/hermes-fleet-policy.json
test -x tests/reliability/fleet-failover.py

python3 -c "import yaml"

python3     tests/reliability/fleet-failover.py

echo "RELIABILITY_TESTS: PASS"
