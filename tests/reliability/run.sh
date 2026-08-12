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
test -f tests/reliability/worker-fallback.py
test -f tests/reliability/task-profile.py
test -x tests/reliability/messaging-delivery.py
test -x tests/reliability/self-repair-rollback.sh
test -x tests/reliability/self-repair-sandbox.sh
test -x tests/reliability/service-persistence.sh

HERMES_PYTHON="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"
if [ ! -x "$HERMES_PYTHON" ]; then HERMES_PYTHON="$(command -v python3)"; fi

"$HERMES_PYTHON" -c "import yaml"

"$HERMES_PYTHON" tests/reliability/fleet-failover.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}" "$HERMES_PYTHON" tests/reliability/task-profile.py
CRON_HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

if [ -f "$CRON_HERMES_ROOT/cron/scheduler_provider.py" ] && \
   [ -f "$CRON_HERMES_ROOT/cron/jobs.py" ] && \
   [ -f "$CRON_HERMES_ROOT/cron/scheduler.py" ] && \
   [ -f "$CRON_HERMES_ROOT/gateway/run.py" ]; then

    PYTHONDONTWRITEBYTECODE=1 \
    OPEN_CLOUD_HERMES_ROOT="$CRON_HERMES_ROOT" \
        "$HERMES_PYTHON" tests/reliability/task-profile-cron.py

else

    echo "TASK_PROFILE_CRON_RELIABILITY: SKIP (Hermes source unavailable)"

fi
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-registry-state.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-verifier-timeout.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-runtime-config.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-lock-concurrency.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/vellum-worker-state.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/daemon-pool-compat.py

HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

if [ -f "$HERMES_ROOT/tools/delegate_tool.py" ]; then

    PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT"         python3         tests/reliability/hermes-concurrency.py

    PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT"         "$HERMES_PYTHON"         tests/reliability/worker-fallback.py

    PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT"         "$HERMES_PYTHON"         tests/reliability/messaging-delivery.py

else

    echo "HERMES_CONCURRENCY_RELIABILITY: SKIP (Hermes source unavailable)"
fi

./tests/reliability/self-repair-rollback.sh

./tests/reliability/service-persistence.sh

echo "RELIABILITY_TESTS: PASS"
