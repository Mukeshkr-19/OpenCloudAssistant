#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant reliability tests"

test -f integrations/fleet/dispatcher.py
test -f config/fleet/hermes-fleet-policy.json
test -f config/hermes/orchestration.json
test -x tests/reliability/fleet-failover.py
test -x tests/reliability/routing-v1-workload.py
test -x tests/reliability/hermes-routing-v1-compat.py
test -x tests/reliability/hermes-concurrency.py
test -f tests/reliability/worker-fallback.py
test -f tests/reliability/task-profile.py
test -f tests/reliability/cron-tool-safety.py
test -f tests/reliability/cron-output-contract.py
test -f tests/reliability/provider-metadata-guard.py
test -f tests/reliability/opencloud-self-repair.py
test -f tests/reliability/guarded-self-heal.py
test -f tests/reliability/guarded-self-heal-detect.py
test -x tests/reliability/guarded-self-heal-e2e.sh
test -f tests/reliability/cron-duplicate-guard.py
test -f tests/reliability/cron-workflow-identity.py
test -f tests/reliability/cron-repeat-coercion.py
test -f tests/reliability/cron-run-now-once.py
test -f tests/reliability/cron-control-fast-path.py
test -f tests/reliability/cron-career-geography.py
test -f tests/reliability/cron-career-search-waves.py
test -f tests/reliability/cron-career-candidate-rejection.py
test -f tests/reliability/cron-search-reliability.py
test -f tests/reliability/provider-cron-survival.py
test -f tests/reliability/cron-baseline-fetch.py
test -f tests/reliability/greeting-output-contract.py
test -f tests/reliability/product-reliability-ux.py
test -f tests/reliability/imessage-model-control-turn-recovery.py
test -x tests/reliability/cron-routing-v1.py
test -x tests/reliability/messaging-delivery.py
test -x tests/reliability/self-repair-rollback.sh
test -x tests/reliability/self-repair-sandbox.sh
test -x tests/reliability/service-persistence.sh
test -f tests/reliability/prepare-hermes.sh
test -x tests/reliability/hermes-fixture-prep.sh

# Hermetic Hermes: explicit OPEN_CLOUD_HERMES_ROOT, else pin + install/35 temp tree.
# Never silently default deterministic reliability to ~/.hermes/hermes-agent.
# shellcheck source=tests/reliability/prepare-hermes.sh
source "$ROOT/tests/reliability/prepare-hermes.sh"
prepare_opencloud_reliability_hermes

test -n "${OPEN_CLOUD_HERMES_ROOT:-}"
test -n "${HERMES_PYTHON:-}"
test -x "$HERMES_PYTHON"
"$HERMES_PYTHON" -c "import yaml"

# Regression must run with a clean env so it does not inherit this suite's root.
./tests/reliability/hermes-fixture-prep.sh

# Materialize-backed product UX / iMessage control tests first so a stale live
# Hermes checkout cannot block them behind live-tree-only checks.
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/product-reliability-ux.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/greeting-output-contract.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/guarded-self-heal.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/guarded-self-heal-detect.py
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    python3 tests/reliability/imessage-model-control-turn-recovery.py

"$HERMES_PYTHON" tests/reliability/fleet-failover.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/routing-v1-workload.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/hermes-routing-v1-compat.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/task-profile.py

# Fail loudly if the prepared tree lacks cron sources (no SKIP / personal fallback).
test -f "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler_provider.py"
test -f "$OPEN_CLOUD_HERMES_ROOT/cron/jobs.py"
test -f "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py"
test -f "$OPEN_CLOUD_HERMES_ROOT/gateway/run.py"
test -f "$OPEN_CLOUD_HERMES_ROOT/tools/delegate_tool.py"

PYTHONDONTWRITEBYTECODE=1 \
OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/task-profile-cron.py

PYTHONDONTWRITEBYTECODE=1 \
OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-routing-v1.py

PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-registry-state.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-verifier-timeout.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-runtime-config.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/fleet-lock-concurrency.py
PYTHONDONTWRITEBYTECODE=1 "$HERMES_PYTHON" tests/reliability/vellum-worker-state.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/daemon-pool-compat.py

PYTHONDONTWRITEBYTECODE=1 python3 tests/reliability/cron-baseline-fetch.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    python3 tests/reliability/hermes-concurrency.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/worker-fallback.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-tool-safety.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-output-contract.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/provider-metadata-guard.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/opencloud-self-repair.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-duplicate-guard.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-workflow-identity.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-repeat-coercion.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-run-now-once.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-control-fast-path.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-career-geography.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-career-search-waves.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-career-candidate-rejection.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/cron-search-reliability.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/provider-cron-survival.py

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HERMES_ROOT="$OPEN_CLOUD_HERMES_ROOT" \
    "$HERMES_PYTHON" tests/reliability/messaging-delivery.py

./tests/reliability/self-repair-rollback.sh

./tests/reliability/guarded-self-heal-e2e.sh

./tests/reliability/service-persistence.sh

echo "RELIABILITY_TESTS: PASS"
