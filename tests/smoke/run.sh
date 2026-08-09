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

echo "SMOKE_TESTS: PASS"
