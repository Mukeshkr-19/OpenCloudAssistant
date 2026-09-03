#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant Fleet runtime smoke test"

DISPATCHER="integrations/fleet/dispatcher.py"
POLICY="config/fleet/hermes-fleet-policy.json"

test -f "$DISPATCHER"
test -f "$POLICY"

python3 -m py_compile "$DISPATCHER"
python3 -m json.tool "$POLICY" >/dev/null

grep -qi "sqlite" "$DISPATCHER"
grep -qi "provider" "$DISPATCHER"
grep -qi "nvidia" "$DISPATCHER"
grep -qi "gemini" "$DISPATCHER"
grep -qi "health" "$DISPATCHER"
grep -qi "cooldown" "$DISPATCHER"
grep -qF "def _runtime_gemini_models" "$DISPATCHER"

grep -qF "openrouter/free" "$POLICY"

grep -qF '"gemini-emergency"' "$POLICY"
grep -qF '"automaticPenalty"' "$POLICY"
! grep -RqsF "HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1" integrations/hermes

echo "FLEET_RUNTIME_SMOKE: PASS"
