#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

HERMES_SOURCE="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

echo "Open Cloud Assistant smoke tests"

bash -n scripts/public-audit.sh
python3 -m py_compile scripts/public-audit.py

test -f .gitignore
test -f .env.example
test -x scripts/public-audit.sh

./scripts/public-audit.sh


"$ROOT/tests/smoke/public-brain.sh"

"$ROOT/tests/smoke/self-repair.sh"

"$ROOT/tests/smoke/materialization.sh"

"$ROOT/tests/smoke/fleet-runtime.sh"
"$ROOT/tests/smoke/fleet-install.sh"
"$ROOT/tests/smoke/fleet-registry.sh"
"$ROOT/tests/smoke/hermes-vellum.sh"
"$ROOT/tests/smoke/task-profile.sh"
"$ROOT/tests/smoke/channels.sh"
"$ROOT/tests/smoke/services.sh"
if [ -d "$HERMES_SOURCE/.git" ]; then
    "$ROOT/tests/smoke/hermes-live.sh"
else
    echo "HERMES_LIVE_INSTALL_SMOKE: SKIP (Hermes Git source unavailable)"
fi
"$ROOT/tests/smoke/setup-install.sh"
"$ROOT/tests/smoke/prerequisites.sh"

"$ROOT/tests/smoke/uninstall.sh"

"$ROOT/tests/smoke/doctor-runtime.sh"

"$ROOT/tests/smoke/release.sh"

./tests/smoke/operational-evidence.sh

echo "SMOKE_TESTS: PASS"
