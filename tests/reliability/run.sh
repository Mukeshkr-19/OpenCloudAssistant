#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant reliability tests"

test -f integrations/fleet/dispatcher.py
test -f config/fleet/hermes-fleet-policy.json
test -f config/hermes/orchestration.json
test -x tests/reliability/fleet-failover.py
test -x tests/reliability/hermes-concurrency.py
test -x tests/reliability/self-repair-rollback.sh
test -x tests/reliability/self-repair-sandbox.sh
test -x tests/reliability/service-persistence.sh

python3 -c "import yaml"

python3     tests/reliability/fleet-failover.py

HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

if [ -f "$HERMES_ROOT/tools/delegate_tool.py" ]; then

    OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT"         python3         tests/reliability/hermes-concurrency.py

else

    echo "HERMES_CONCURRENCY_RELIABILITY: SKIP (Hermes source unavailable)"
fi

./tests/reliability/self-repair-rollback.sh

./tests/reliability/service-persistence.sh

echo "RELIABILITY_TESTS: PASS"
