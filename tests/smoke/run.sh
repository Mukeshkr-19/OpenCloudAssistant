#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

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
echo "SMOKE_TESTS: PASS"
